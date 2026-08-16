# 新闻传播学知识图谱 (News Journalism KG)

一个专注于新闻学与传播学领域的知识图谱项目，收录中西方新闻传播学者、理论、著作及其学术影响关系。

## 项目特色

- **双语支持**：中英文对照的学者信息
- **中西并重**：同时收录西方传播学和中国新闻史人物
- **证据追溯**：每一条断言都有公开文献支撑
- **可视化**：支持知识图谱、影响网络、时间轴等多种可视化方式
- **数据开放**：支持导出 JSON 和 RDF/Turtle 格式

## 快速开始

### 1. 启动本地服务器

```bash
# Windows
python workbench\server.py

# 或者使用脚本
run_workbench.bat
```

服务器将在 http://127.0.0.1:8125 启动。

### 2. 构建静态站点

```bash
python scripts/build_static.py
```

构建后的静态文件将在 `dist/` 目录中，可部署到 Netlify 等平台。

### 3. 数据校验

```bash
python scripts/validate_csv.py
```

## 项目结构

```
news-journalism-kg/
├── config.json              # 项目配置
├── data/
│   └── csv/                 # CSV 数据文件
│       ├── SCHEMA.md        # 数据结构文档
│       ├── scholars.csv     # 学者信息
│       ├── propositions.csv # 理论命题
│       ├── passages.csv     # 证据段落
│       ├── influences.csv   # 影响关系
│       ├── books.csv        # 经典著作
│       ├── concepts.csv     # 概念定义
│       ├── years.csv        # 年份数据
│       └── relations.csv    # 社会关系
├── dist/                    # 静态构建输出
├── scripts/
│   ├── validate_csv.py      # CSV 校验脚本
│   └── build_static.py      # 静态构建脚本
├── workbench/
│   ├── server.py            # Python HTTP 服务器
│   └── static/
│       ├── index.html       # 主页面
│       ├── style.css        # 样式文件
│       └── app.js           # 前端应用
├── run_workbench.bat        # Windows 启动脚本
├── run_workbench.ps1        # PowerShell 启动脚本
└── netlify.toml             # Netlify 部署配置
```

## 数据模型

### 核心实体

| 实体 | 说明 |
|------|------|
| Scholar | 学者 - 新闻传播领域的思想家 |
| Proposition | 命题 - 学者提出的理论断言 |
| Passage | 段落 - 来自公开文献的证据 |
| Influence | 影响 - 学术传承与影响关系 |
| Book | 著作 - 经典学术著作 |
| Concept | 概念 - 核心理论概念 |

### 学派分类

项目收录了以下学派的学者：

- **哥伦比亚学派** (SCHOOL_COLUMBIA) - 拉扎斯菲尔德、施拉姆等
- **多伦多学派** (SCHOOL_TORONTO) - 麦克卢汉、伊尼斯
- **法兰克福学派** (SCHOOL_FRANKFURT) - 哈贝马斯
- **芝加哥学派** (SCHOOL_CHICAGO) - 拉斯韦尔
- **伯明翰学派** (SCHOOL_BIRMINGHAM) - 霍尔
- **中国改良派** (SCHOOL_CN_REFORM) - 梁启超、康有为、严复
- **中国革命派** (SCHOOL_CN_REVOLUTION) - 陈独秀、李大钊
- **中国民国派** (SCHOOL_CN_REPUBLIC) - 邵飘萍、张季鸾、史量才
- 以及更多...

## 功能模块

### 概览 (Dashboard)
展示统计数据和快速入口。

### 学者名录 (Scholars)
按学派筛选浏览所有学者，点击可查看详情（含学者金句）。

### 历史时间轴 (Timeline)
按年份展示理论命题和历史事件，支持「中西对照」双栏模式。

### 知识图谱 (Graph)
可视化学者与学派的关系网络，单击聚焦影响半径，双击查看详情。

### 影响网络 (Influence)
展示学术传承与影响关系的有向图，支持「时间旅行」滑块观看网络随年代生长。

### 经典著作 (Books)
收录改变历史的学术名著。

### 核心概念 (Concepts)
概念卡片与「关联网络」视图（概念共现图谱）。

### 主题导览 (Tours)
策展式故事线：言论自由之路、媒介环境学之旅、从拟态环境到过滤气泡。

### 知识竞答 (Quiz)
从命题/著作数据自动生成选择题，记录历史最佳成绩。

### 连接游戏 (Game)
「六度连接」猜谜：猜测两位学者间的影响路径步数，用真实学术传承验证。

### 辩论场 (Debates)
李普曼 vs 杜威、拉扎斯菲尔德 vs 阿多诺等经典论战的对峙视图。

### 成就 (Achievements)
浏览学者、竞答、游戏、导览均可解锁成就，进度保存在本地。

### 证据库 (Browse)
展示所有文献证据段落（含来源链接）。

### 数据录入 (Data)
在线编辑 CSV 数据文件。

### 校验工具 (Validate)
检查数据完整性和外键引用。

## 技术栈

- **后端**：Python 3.12+ (纯标准库)
- **前端**：原生 HTML/CSS/JavaScript
- **图形库**：Vis.js
- **字体**：Merriweather + Inter (Google Fonts)
- **部署**：Netlify (静态托管)

## 设计原则

1. **证据可追溯**：所有命题必须绑定证据段落
2. **年份精确性**：事件和命题精确到年份
3. **影响可解释**：影响关系需有文献支持
4. **人可维护**：CSV 格式允许非程序员直接编辑

## 许可证

MIT License

## 贡献

欢迎提交 Pull Request 或 Issue！

---

**新闻传播学知识图谱** - 探索新闻学与传播学的学术脉络
