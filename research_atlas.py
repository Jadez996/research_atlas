from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd
import requests
import yaml
from pyvis.network import Network

BASE = "https://api.openalex.org"
ALLOWED_DOMAINS = [
    "Physics",
    "Materials Science",
    "Chemistry",
    "Engineering",
]


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    required = ["field_name", "queries", "from_year"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(f"Missing config keys: {', '.join(missing)}")
    for key in ("author_blacklist", "topic_blacklist"):
        values = cfg.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"Config key '{key}' must be a list of strings")
    return cfg


class OpenAlexClient:
    def __init__(self, mailto: str = "", api_key: str = "") -> None:
        self.session = requests.Session()
        self.common = {}
        if mailto and mailto != "your_email@example.com":
            self.common["mailto"] = mailto
        if api_key:
            self.common["api_key"] = api_key

    def get(self, endpoint: str, params: dict[str, Any], retries: int = 5) -> dict[str, Any]:
        merged = {**params, **self.common}
        delay = 1.0
        for attempt in range(retries):
            response = self.session.get(f"{BASE}{endpoint}", params=merged, timeout=60)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == retries - 1:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else delay)
                delay = min(delay * 2, 30)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("OpenAlex request failed")

    def search_works(self, query: str, from_year: int, limit: int) -> list[dict[str, Any]]:
        works: list[dict[str, Any]] = []
        cursor = "*"
        while len(works) < limit:
            page_size = min(100, limit - len(works))
            data = self.get(
                "/works",
                {
                    "search": query,
                    "filter": f"from_publication_date:{from_year}-01-01",
                    "sort": "cited_by_count:desc",
                    "per_page": page_size,
                    "cursor": cursor,
                    "select": "id,title,publication_year,cited_by_count,authorships,primary_topic,doi",
                },
            )
            batch = data.get("results", [])
            if not batch:
                break
            works.extend(batch)
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(0.12)
        return works


def short_id(value: str | None) -> str:
    return (value or "").rsplit("/", 1)[-1]


def filter_works_by_topic(works: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    blocked_topics = [topic.strip().casefold() for topic in cfg.get("topic_blacklist", []) if topic.strip()]
    if not blocked_topics:
        return works
    return [
        work for work in works
        if not any(
            blocked in ((work.get("primary_topic") or {}).get("display_name") or "").casefold()
            for blocked in blocked_topics
        )
    ]


def filter_works_by_domain(works: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_domains = {domain.casefold() for domain in ALLOWED_DOMAINS}
    allowed_domains.add("physics and astronomy")
    filtered = []
    for work in works:
        primary_topic = work.get("primary_topic") or {}
        classifications = (
            primary_topic.get("field"),
            primary_topic.get("subfield"),
        )
        if any(
            isinstance(classification, dict)
            and (classification.get("display_name") or "").casefold() in allowed_domains
            for classification in classifications
        ):
            filtered.append(work)
    return filtered


def deduplicate_works(works: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {}
    for work in works:
        if work.get("id"):
            by_id[work["id"]] = work
    return list(by_id.values())


def analyse(works: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, nx.Graph]:
    now_year = datetime.now(timezone.utc).year
    recent_start = now_year - int(cfg.get("rising_window_years", 5)) + 1
    works = filter_works_by_topic(works, cfg)
    works = filter_works_by_domain(works)
    author_blacklist = cfg.get("author_blacklist", [])
    blacklisted_names = {value.strip().casefold() for value in author_blacklist}
    blacklisted_ids = {short_id(value).casefold() for value in author_blacklist}
    authors: dict[str, dict[str, Any]] = {}
    institution_stats: dict[str, dict[str, Any]] = {}
    graph = nx.Graph()

    for work in works:
        year = int(work.get("publication_year") or 0)
        citations = int(work.get("cited_by_count") or 0)
        topic = (work.get("primary_topic") or {}).get("display_name", "")
        work_authors = []

        for authorship in work.get("authorships") or []:
            author = authorship.get("author") or {}
            aid = short_id(author.get("id"))
            if not aid:
                continue
            name = author.get("display_name") or aid
            is_blocked_author = (
                aid.casefold() in blacklisted_ids or name.strip().casefold() in blacklisted_names
            )
            institutions = authorship.get("institutions") or []
            inst_names = [i.get("display_name") for i in institutions if i.get("display_name")]
            inst_ids = [short_id(i.get("id")) for i in institutions if i.get("id")]
            if not is_blocked_author:
                rec = authors.setdefault(aid, {
                    "author_id": aid, "name": name, "works": 0, "citations": 0,
                    "recent_works": 0, "recent_citations": 0,
                    "institutions": Counter(), "topics": Counter(), "years": Counter()
                })
                rec["works"] += 1
                rec["citations"] += citations
                rec["years"][year] += 1
                if year >= recent_start:
                    rec["recent_works"] += 1
                    rec["recent_citations"] += citations
                if topic:
                    rec["topics"][topic] += 1
            for iid, iname in zip(inst_ids, inst_names):
                if not is_blocked_author:
                    rec["institutions"][iname] += 1
                inst = institution_stats.setdefault(iid or iname, {
                    "institution_id": iid, "institution": iname, "works": set(),
                    "fractional_works": 0.0, "citations": 0, "authors": Counter()
                })
                inst["works"].add(work.get("id"))
                inst["fractional_works"] += 1 / max(len(inst_names), 1)
                inst["citations"] += citations
                if not is_blocked_author:
                    inst["authors"][name] += 1
            if not is_blocked_author:
                work_authors.append((aid, name))

        unique = list(dict(work_authors).items())
        for aid, name in unique:
            graph.add_node(aid, label=name)
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                a, b = unique[i][0], unique[j][0]
                if graph.has_edge(a, b):
                    graph[a][b]["weight"] += 1
                else:
                    graph.add_edge(a, b, weight=1)

    min_works = int(cfg.get("min_author_works", 3))
    eligible = {aid for aid, rec in authors.items() if rec["works"] >= min_works}
    graph = graph.subgraph(eligible).copy()
    weighted_degree = dict(graph.degree(weight="weight"))
    pagerank = nx.pagerank(graph, weight="weight") if graph.number_of_nodes() else {}

    rows = []
    for aid in eligible:
        rec = authors[aid]
        career_years = max(1, len([y for y in rec["years"] if y]))
        recent_share = rec["recent_works"] / rec["works"]
        impact = math.log1p(rec["citations"])
        activity = math.log1p(rec["works"])
        network = math.log1p(weighted_degree.get(aid, 0))
        influence_score = 0.45 * impact + 0.25 * activity + 0.20 * network + 0.10 * math.log1p(1000 * pagerank.get(aid, 0))
        rising_score = (rec["recent_works"] / career_years) * (1 + math.log1p(rec["recent_citations"])) * (0.5 + recent_share)
        rows.append({
            "author_id": aid,
            "name": rec["name"],
            "primary_institution": rec["institutions"].most_common(1)[0][0] if rec["institutions"] else "Unknown",
            "works_in_dataset": rec["works"],
            "citations_of_dataset_works": rec["citations"],
            "recent_works": rec["recent_works"],
            "coauthor_strength": round(weighted_degree.get(aid, 0), 2),
            "pagerank": round(pagerank.get(aid, 0), 6),
            "influence_score": round(influence_score, 4),
            "rising_score": round(rising_score, 4),
            "top_topics": "; ".join(x for x, _ in rec["topics"].most_common(3)),
            "openalex_url": f"https://openalex.org/{aid}",
        })

    author_df = pd.DataFrame(rows)
    if not author_df.empty:
        author_df = author_df.sort_values(["influence_score", "citations_of_dataset_works"], ascending=False)
        author_df.insert(0, "rank", range(1, len(author_df) + 1))

    inst_rows = []
    for rec in institution_stats.values():
        inst_rows.append({
            "institution_id": rec["institution_id"],
            "institution": rec["institution"],
            "works": len(rec["works"]),
            "fractional_authorship_count": round(rec["fractional_works"], 2),
            "citation_sum_not_deduplicated": rec["citations"],
            "top_authors": "; ".join(x for x, _ in rec["authors"].most_common(5)),
        })
    inst_df = pd.DataFrame(inst_rows)
    if not inst_df.empty:
        inst_df = inst_df.sort_values(["works", "fractional_authorship_count"], ascending=False)
        inst_df.insert(0, "rank", range(1, len(inst_df) + 1))
    return author_df, inst_df, graph


def write_network(graph: nx.Graph, author_df: pd.DataFrame, path: Path, top_n: int) -> None:
    if author_df.empty:
        path.write_text("<html><body><p>No authors matched.</p></body></html>", encoding="utf-8")
        return
    keep = set(author_df.head(top_n)["author_id"])
    g = graph.subgraph(keep).copy()
    net = Network(height="850px", width="100%", bgcolor="#0f172a", font_color="#e2e8f0", cdn_resources="remote")
    net.barnes_hut(gravity=-18000, central_gravity=0.25, spring_length=160, spring_strength=0.03, damping=0.09)
    ranks = dict(zip(author_df["author_id"], author_df["rank"]))
    insts = dict(zip(author_df["author_id"], author_df["primary_institution"]))
    for node, data in g.nodes(data=True):
        degree = g.degree(node, weight="weight")
        title = f"{html.escape(data.get('label', node))}<br>{html.escape(insts.get(node, 'Unknown'))}<br>Rank: {ranks.get(node, '')}"
        net.add_node(node, label=data.get("label", node), title=title, value=max(5, math.sqrt(degree + 1) * 5))
    for a, b, data in g.edges(data=True):
        net.add_edge(a, b, value=data.get("weight", 1), title=f"Shared works: {data.get('weight', 1)}")
    net.write_html(str(path), open_browser=False)


def write_summary(author_df: pd.DataFrame, inst_df: pd.DataFrame, works_n: int, cfg: dict[str, Any], out: Path) -> None:
    lines = [
        f"# Research Atlas: {cfg['field_name']}", "",
        f"Updated: {datetime.now(timezone.utc).isoformat()}",
        f"Unique works analysed: {works_n}",
        f"Queries: {', '.join(cfg['queries'])}", "",
        "## Important researchers", "",
    ]
    for _, r in author_df.head(20).iterrows():
        lines.append(f"- **{r['name']}** — {r['primary_institution']}; works {r['works_in_dataset']}; dataset-work citations {r['citations_of_dataset_works']}; [OpenAlex]({r['openalex_url']})")
    lines += ["", "## Rising researchers (signal, not a career-stage judgement)", ""]
    rising = author_df.sort_values("rising_score", ascending=False).head(20) if not author_df.empty else author_df
    for _, r in rising.iterrows():
        lines.append(f"- **{r['name']}** — recent works {r['recent_works']}; rising score {r['rising_score']}; {r['primary_institution']}")
    lines += ["", "## Institutions", ""]
    for _, r in inst_df.head(20).iterrows():
        lines.append(f"- **{r['institution']}** — works {r['works']}; top authors: {r['top_authors']}")
    lines += ["", "## Interpretation cautions", "",
        "- Results depend on the search queries and OpenAlex author disambiguation.",
        "- Citation counts favour older work and vary by subfield.",
        "- The rising score measures recent activity inside this dataset; it does not infer age, job level, or research quality.",
        "- Institutional affiliation is inferred from affiliations attached to matched works, so historical and current affiliations may mix.",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a metadata-only researcher atlas from OpenAlex")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default="output")
    args = parser.parse_args()
    cfg = load_config(args.config)
    api_key = os.getenv("OPENALEX_API_KEY", cfg.get("openalex_api_key", ""))
    client = OpenAlexClient(cfg.get("mailto", ""), api_key)
    all_works = []
    for query in cfg["queries"]:
        print(f"Searching: {query}")
        all_works.extend(client.search_works(query, int(cfg["from_year"]), int(cfg.get("max_works_per_query", 500))))
    works = deduplicate_works(all_works)
    print(f"Unique works: {len(works)}")
    filtered_works = filter_works_by_topic(works, cfg)
    if len(filtered_works) != len(works):
        print(f"Works excluded by topic blacklist: {len(works) - len(filtered_works)}")
    domain_filtered_works = filter_works_by_domain(filtered_works)
    if len(domain_filtered_works) != len(filtered_works):
        print(f"Works excluded by discipline filter: {len(filtered_works) - len(domain_filtered_works)}")
    works = domain_filtered_works

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "works_metadata.json", "w", encoding="utf-8") as f:
        json.dump(works, f, ensure_ascii=False, indent=2)
    author_df, inst_df, graph = analyse(works, cfg)
    author_df.to_csv(out / "researchers.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    inst_df.to_csv(out / "institutions.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    nx.write_gexf(graph, out / "collaboration_network.gexf")
    write_network(graph, author_df, out / "collaboration_network.html", int(cfg.get("top_n", 100)))
    write_summary(author_df, inst_df, len(works), cfg, out / "SUMMARY.md")
    print(f"Done. Results written to {out.resolve()}")


if __name__ == "__main__":
    main()
