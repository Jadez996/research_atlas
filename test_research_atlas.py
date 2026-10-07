import unittest

from research_atlas import analyse, filter_works_by_topic


def make_work(work_id, topic, authors):
    return {
        "id": work_id,
        "publication_year": 2024,
        "cited_by_count": 10,
        "primary_topic": {"display_name": topic},
        "authorships": [
            {
                "author": {"id": f"https://openalex.org/{author_id}", "display_name": name},
                "institutions": [
                    {"id": "https://openalex.org/I1", "display_name": "Example University"}
                ],
            }
            for author_id, name in authors
        ],
    }


class BlacklistTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "min_author_works": 1,
            "topic_blacklist": [
                "Heart Failure Treatment and Management",
                "Cardiac and Coronary Surgery Techniques",
            ],
            "author_blacklist": ["A2"],
        }
        self.works = [
            make_work(
                f"water-{index}",
                "Nanofluidics and water transport",
                [("A1", "Target Researcher"), ("A2", "Manually Excluded")],
            )
            for index in range(3)
        ] + [
            make_work(
                "heart-failure",
                "Heart Failure Treatment and Management",
                [("A3", "Cardiology Researcher")],
            ),
            make_work(
                "cardiac-surgery",
                "Cardiac and Coronary Surgery Techniques",
                [("A4", "Surgery Researcher")],
            ),
        ]

    def test_topic_blacklist_filters_work_before_analysis(self):
        filtered = filter_works_by_topic(self.works, self.cfg)
        self.assertEqual(len(filtered), 3)

        authors, institutions, graph = analyse(self.works, self.cfg)
        self.assertEqual(authors["name"].tolist(), ["Target Researcher"])
        self.assertEqual(set(graph.nodes), {"A1"})
        self.assertNotIn("Manually Excluded", institutions.iloc[0]["top_authors"])
        self.assertNotIn("Cardiology Researcher", authors["name"].tolist())
        self.assertNotIn("Surgery Researcher", authors["name"].tolist())

    def test_author_blacklist_accepts_full_name_case_insensitively(self):
        cfg = {**self.cfg, "author_blacklist": ["manually excluded"]}
        authors, _, _ = analyse(self.works, cfg)
        self.assertEqual(authors["name"].tolist(), ["Target Researcher"])


if __name__ == "__main__":
    unittest.main()
