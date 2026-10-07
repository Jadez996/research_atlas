# Research Atlas Free

一个只使用学术元数据、不下载论文 PDF 的免费研究者数据库生成器。它通过 OpenAlex 检索论文元数据，输出研究者、机构、合作网络以及“近期活跃信号”。Chatgpt生成，测试。

## 输出

- `output/researchers.csv`: 研究者排名、机构、数据集内论文数和引用、合作网络指标
- `output/institutions.csv`: 机构统计
- `output/collaboration_network.html`: 可交互合作网络
- `output/collaboration_network.gexf`: 可用 Gephi 打开
- `output/SUMMARY.md`: 可直接放进 Obsidian
- `output/works_metadata.json`: 仅元数据，不含 PDF 或全文

## 本地运行

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python research_atlas.py
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python research_atlas.py
```

完成后用浏览器打开 `output/collaboration_network.html`。

## 修改研究领域

编辑 `config.yaml`。不要只放一个宽泛关键词，建议使用 3 至 6 个相互补充的短语。例如：

```yaml
field_name: angstrom-nanofluidics
queries:
  - "angstrom-scale channels"
  - "two-dimensional nanofluidics"
  - "confined water dielectric"
  - "proton transport two-dimensional channels"
from_year: 2000
max_works_per_query: 500
min_author_works: 3
top_n: 100
rising_window_years: 5
mailto: "your_email@example.com"
openalex_api_key: ""
```

`max_works_per_query` 越大，覆盖越广，但 API 请求和噪声也越多。第一次建议 300 至 500。

### 排除不相关主题和作者

程序只保留 OpenAlex 学科分类中属于 Physics、Materials Science、Chemistry 或 Engineering 的论文，其他领域（包括医学）会在研究者、机构和合作网络统计前过滤。Physics 同时匹配 OpenAlex 的 “Physics and Astronomy” 分类。允许的学科列表定义在 `research_atlas.py` 的 `ALLOWED_DOMAINS` 中。

可通过 `topic_blacklist` 排除 OpenAlex 论文主主题包含指定词语的论文。程序会在统计研究者、机构和合作网络之前过滤这些论文；主题文字不区分大小写，按包含关系匹配。当前配置排除了心衰治疗和心脏/冠脉外科主题。

若某位研究者仍出现在结果中，可将其 OpenAlex 作者 ID 或完整姓名加入 `author_blacklist`：

```yaml
topic_blacklist:
  - "Heart Failure Treatment and Management"
  - "Cardiac and Coronary Surgery Techniques"
author_blacklist:
  - "A5017272571"
  - "Researcher Full Name"
```

作者姓名按完整名称、不区分大小写匹配；优先使用 OpenAlex ID，避免同名误排。调整查询词以更贴近目标课题，并配合黑名单过滤能进一步降低检索噪声。修改 `config.yaml` 后重新运行程序以更新输出。

## GitHub Actions 自动更新

1. 在 GitHub 新建一个空仓库。
2. 把本项目所有文件推送到仓库。
3. 打开仓库的 `Actions` 页面，运行 `Update Research Atlas`。
4. 工作流默认每周一 05:17 UTC 自动运行并提交 `output/` 的变化。
5. 可选：在 OpenAlex 注册免费 API key，然后在仓库 `Settings > Secrets and variables > Actions` 新建 `OPENALEX_API_KEY`。没有 key 也可以进行基础查询。

如果 GitHub 阻止工作流推送，请到 `Settings > Actions > General > Workflow permissions` 选择 `Read and write permissions`。

## 排名逻辑

- `influence_score`: 数据集内引用、论文数、合作强度和 PageRank 的组合，用于发现历史或当前有影响力的人。
- `rising_score`: 最近若干年的论文活动、相关引用和近期论文占比的组合。
- 它们只用于缩小人工核查范围，不等同于研究质量、职级或年龄判断。

## 重要限制

1. 检索词决定边界。建议多组查询并人工检查前 50 名。
2. OpenAlex 的作者消歧和机构信息可能有错误。
3. `primary_institution` 是匹配论文中出现最多的机构，不保证是当前任职机构。
4. 引用指标天然偏向较老论文。
5. 作者极多的大型合作论文会制造密集边。特定领域可进一步设置最大作者数过滤。

## 免费性

程序本身使用开源 Python 包。OpenAlex 支持免 key 的基础 API 查询，也提供免费 API key；实际额度和政策以 OpenAlex 官方说明为准。
