# 项目命名 — Verisaria

> 定名于 2026-06。本文件记录项目名 **Verisaria** 的来源与含义。

## 名字

**Verisaria** （`verisaria`，Python 包名）

读音：veh-ri-SAH-ria（重音在第三音节）。

## 来源:Verisim × Causaria 的融合

定名时在两个候选之间权衡:

- **Verisim** —— 取自 *verisimilitude*(逼真、看似真实)+ *sim*(模拟)。指向项目的**初心**:让一个世界**看起来是真的**(用户原话"大世界真实")。
- **Causaria** —— *causa*(因果)+ *-aria*(地名后缀),"因果之地"。指向项目的**核心机制**:玩家的选择会沿三通道(关系/立场/世界事实)涟漪成后果。

**Verisaria** 是两者的融合——**采用 Verisim 的「含义」(veri-,真实)+ Causaria 的「形与音」(-aria 地名后缀)**:

> veri-(真) + -saria(承自 Cau*saria* 的地名后缀) = **"看似为真的国度"**

它不是生硬的拼接,而像是与 Causaria 同族的一个真实地名;既保留了"因果之地"的严肃模拟器气质,又悄悄把"让世界显得真实"这一北极星编码进了名字本身。

## 为什么是它

- **命名的是使命,而非某种风格**:本项目是一个**通用的、内容包驱动的世界引擎**,不该被某种奇幻基调(矮人要塞/低魔边境…)锁死。Verisaria 指向"让世界可信"这件引擎要做的事,而非某个具体世界的味道。
- **不走 ai-/llm- 的老路**:刻意避开 `ai-rpg`/`llm-rpg` 这类技术前缀,选了一个原创、可发音、像真实世界名的词。
- **编码了项目的两根支柱**:**真实感(A5 分层真相、活的 NPC)** 与 **因果(选择→后果三通道)**——前者来自 *veri*,后者来自 *Causaria* 的血缘。

## 落地

顶层 Python 包从 `rpg_demo` 改名为 `verisaria`,同时分层为
`engine/ · runtime/ · protocol/ · frontends/`(见 [design/protocol-design.md](design/protocol-design.md))。
