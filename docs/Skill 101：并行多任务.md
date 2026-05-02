答谢读者
没想到《如何写好 Skill》这个系列在社区里受到了广泛传播，上一篇文章不到 2 天就破了 2,000 阅读量。今天我就赶紧来写这个系列的第二篇——进阶篇：并行多任务。
声明：并不是所有的 Agent 平台都具备并行多任务能力，例如 claude.ai 网站不支持并行，而 Claude Cowork、Claude Code 则支持。其中，Claude Code 目前已经可以自主决定是否需要启动 Sub-agent，不过通过本文的方法可以更具体的、更稳定的启用子任务。

1. 前言
2025 年 6 月，两篇观点针锋相对的技术博客几乎同时发布：Cognition（Devin 背后的公司）发表了 Don't Build Multi-Agents，而 Anthropic 紧接着发表了 How we built our multi-agent research system。前者劝你不要搞多 Agent，后者却靠多 Agent 把研究任务的性能拉升了 90%。
谁对？都对。关键在于搞清楚一件事：什么样的任务适合并行，什么样的不适合。
笔者最近在为一个通用 EDA（Exploratory Data Analysis）Skill 设计执行架构时，深入思考了这个问题。EDA 就是给 Agent 一个 CSV 或 Excel，让 AI 不断自己命题、自己编写并执行 Python 代码进行查询统计，在认为自己足够了解数据后，撰写最终的报告，是一个标准的 Code-act 过程。本文把并行子任务的价值拆解为三个维度——性能、隔离、多样性——逐一展开，最后讨论何时不该并行以及工程上的取舍。
题外话：什么是 EDA？
EDA（Exploratory Data Analysis，探索性数据分析）是数据分析的"第一眼"——在你建模、下结论之前，先让数据"说话"。它没有固定流程，核心是用统计 + 可视化快速摸清数据的长相：有多少行列、分布是否正常、哪些字段高度相关、哪里有缺失或异常。EDA 的价值不在于给出答案，而在于帮你问出正确的问题。
以下是我对 EDA 这个 Skill 的两种架构设计对比，一种是串行架构，另一种则是并行架构：
[Image]
究竟哪一种架构更适合“自探索式数据分析”呢？让我们一起来探索吧。
https://my.feishu.cn/sync/Al7fdhQBqssP3Fb7uNkczV3Cnse
这篇文章是一篇进阶的 Skill 技巧，可能需要有一定的 AI 和计算机知识，你喜欢吗？
This content is only supported in a Feishu Docs


---

2. 为什么复杂 Skill 要多任务并行
2.1 第一重优势：性能——用时间换智力密度
最直觉的好处是快。
单 Agent 串行执行时，每一步推理都要等上一步完成。对一个典型的 EDA 任务来说，单变量分析、相关性计算、异常检测、趣味发现挖掘，这四块工作之间几乎没有数据依赖。串行执行意味着总时间是四块之和；并行执行则取决于最慢的那块——理论上接近 4 倍加速。
这不是理论推演。M1-Parallel 论文（2025 年发表于 arXiv）在复杂推理任务上实测了并行多团队执行的效果，报告了高达 2.2 倍的端到端加速，同时保持了准确率不下降。而 Anthropic 在其 Research 系统的内部评测中发现，多 Agent 架构（Claude Opus 4 领衔 + Claude Sonnet 4 子 Agent）相比单 Agent Claude Opus 4，在研究任务上的表现提升了 90.2%。
但“快”还只是表层。更深层的性能优势在于智力密度（intelligence density）。Anthropic 的分析揭示了一个惊人的数字：
在 BrowseComp 评测中，token 使用量单独就能解释 80% 的性能差异。换句话说，Research 任务的质量几乎正比于你能投入多少计算量。多 Agent 并行执行的真正价值，是让你能在相同的墙钟时间内，投入数倍的 token——也就是数倍的“思考量”。
笔者按：但也很耗费金钱
[Image]
一个值得记住的心智模型是：多 Agent 并行不是在做同一件事的“多线程”，而是在同一份数据上投入了“多份智力”。这与传统编程中的并行有本质不同——传统并行是把确定性计算拆开加速，LLM 并行是让多个独立的推理过程同时展开，每个过程都可能发现不同的东西。
2.2 第二重优势：上下文隔离——每个子 Agent 都有一间"干净的房间"
这一点经常被忽视，但笔者认为它才是并行子任务最重要的工程价值。
LLM 的上下文窗口是一种稀缺资源。往里面塞的东西越多，模型的注意力就越分散，推理质量就越退化。LangChain 的官方文档对此有一个非常精辟的量化分析：在一个需要比较 Python、JavaScript 和 Rust 的任务中，使用 Subagent 模式（每个子 Agent 只加载自己需要的 2000 token 文档）总共消耗约 9K token；而使用 Skills 模式（所有文档都塞进同一个上下文）则膨胀到 15K token。子 Agent 模式的 token 消耗减少了 67%。
上下文隔离带来三个层次的好处：
第一，注意力聚焦：当一个子 Agent 只负责“异常检测”这一件事时，它的整个上下文窗口都被异常检测相关的数据、代码输出和推理过程所填满。它不需要“记得”相关性分析的中间结果，也不需要“忽略”Fun Facts 挖掘过程中产生的噪音。它拥有一间干净的房间，可以心无旁骛地工作。
第二，降低"上下文污染"风险：在长序列推理中，前面步骤的错误会像传染病一样扩散到后续步骤。一个统计计算中的小错误可能让后续的异常检测产生误判，误判又可能污染最终的 Fun Facts。并行执行天然切断了这条错误传播链——每个子 Agent 从干净的状态出发，互不干扰。
第三，延长有效工作时间：Claude Code 的子 Agent 设计就是一个经典案例。主 Agent 把需要大量阅读和搜索的"调研类"任务交给子 Agent 完成。子 Agent 做完后只返回结论，而不是把整个调研过程的 trace 灌回主 Agent 的上下文。这样主 Agent 的上下文保持精简，能持续工作更长时间而不触及上下文窗口上限。
2.3 第三重优势：多样性——同一个问题的多条路径
LLM 的本质是一个概率采样器。给定相同的 prompt，每次生成的结果都可能不同。在单 Agent 串行执行中，这种随机性是噪声——你只有一条路径，走错了就错了。但在并行执行中，这种随机性变成了资产——多条路径同时展开，总有一条能找到最优解。
[Image]
这正是 M1-Parallel 框架的核心思路：并行运行多个 Agent 团队，每个团队独立制定计划、独立执行，然后通过"早停"（early termination）或"聚合"（aggregation）策略选出最佳结果。实验表明，即使不刻意引导各团队走不同的路径，单纯依靠 LLM 采样的天然多样性，并行执行就已经能显著提升任务完成率。
在 EDA 场景中，这种多样性体现得尤为具体。比如“Fun Facts 挖掘”这个子任务，不同的子 Agent 可能会从完全不同的角度切入数据：一个发现了时间维度上的异常分布，另一个发现了某个分类变量中的 Zipf 定律偏离，第三个注意到了两列之间出人意料的负相关。如果用单 Agent 串行执行，你大概率只会得到最"主流"的那一两个发现——因为模型的注意力被前序步骤的结果所锚定（anchoring effect），很难跳出既有的思维框架。
[Image]
Sub-agent vs Agent Team
聪明的你可能已经想到了，既然可以综合四个 Sub-agent 的视角，是否还可以让不同的 Agent 辩论呢？是的，这时候你需要在 Claude Code 中开启 Agent Team（实验功能）。他们可以先“吵架”再“复合”。事实上，让背后运行不同的模型的 Agent 以及不同 Prompt 的 Agent 在一起讨论，还真的可以得到更全面、更值得推敲的结果，唯一的缺点就是——Agent Team 真的好耗 Token 啊！
多样性的价值在“阅读型”任务中远大于“写作型”任务。这也是 Anthropic 和 Cognition 看似矛盾却实则一致的原因——Anthropic 做的是 Research（阅读、搜索、综合），Cognition 做的是 Coding（写代码、修 bug、集成）。Research 天然适合并行探索，因为搜索空间大且各方向互不干扰；Coding 则对一致性要求极高，并行写出的代码片段很容易互相冲突。
2.4 硬币的另一面：何时不该并行
并行不是万能药。笔者整理了三种应该谨慎使用并行的场景。
场景一：子任务之间存在强依赖。 如果子任务 B 的输入依赖子任务 A 的输出，那并行就失去了意义。在 EDA 中，"数据加载"和"数据质量审计"就是这种关系——你必须先成功加载数据，才能审计数据质量。笔者在 EDA Skill 中把 Phase 0（加载）和 Phase 1（质量审计）设计为串行，只在 Phase 2（深度分析）才展开并行。
场景二：子 Agent 之间需要"商量"。 Cognition 的核心批评就在这里。当多个子 Agent 需要在执行过程中互相协调——比如两个 Agent 写的代码要合并到同一个代码库——并行执行就会产生冲突。今天的 LLM 还不擅长这种跨 Agent 的实时协商。Cognition 的原话很有画面感：这就像让两个从未见过面的工程师各自写一半代码，然后指望第三个人把它们拼在一起。
场景三：上下文共享不充分。 如果 Lead Agent 给子 Agent 的任务描述过于笼统——比如只说"研究半导体短缺"而不指定应该关注哪个时期、哪个细分市场——那多个子 Agent 很可能做出大量重复工作。Anthropic 在早期开发中就踩过这个坑。解法是 Lead Agent 在 spawn 子 Agent 时必须提供非常具体的任务描述，包括调查目标、输出格式、使用的工具和明确的任务边界。

✅ 适合并行：
- 多维度分析
- 多源检索与汇总
- 一份文档的多语言翻译
- 多角度 Review
 ❌ 慎用并行：
- 上下依赖的高耦合任务
- 长链推理
- 制作网页时，并行写 HTML、CSS 和  JS


---

3. 如何在 Skill 中提示
3.1 在 Prompt 里添加一句话
我们通过 skill-creator 创建 Skill 时，你可以这么说来启发 Agent 在 Skill 里用并行：
为提高效率，应显式使用 spawn 这个单词来启发模型启动并行子任务（如单变量分析、双变量分析、数据质量检查、Fun Facts 挖掘等同步推进）。在 SKILL.md 的 Frontmatter 里显式的添加 allowed-tools: Agent Task。 使用多任务运行时，可以遵循“先并行，再串行，再并行，再串行”的模式。注注意并行的任务之间应当尽量的独立不依赖。如果用户开启了CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS，则 Agent 之间可以协作。
spawn 这个单词程序员们一定不会陌生，通常是指 “生成”、“创建” 或 “启动” 一个新的独立执行单元，。而Frontmatter 指的是 SKILL.md 的文件头部中夹在两个“---”横线之间的内容，而 allow-tools 属性是提示 Agent 可以使用哪些工具（用空格分隔），其中 Agent 工具分别是 Claude 用于 Sub-agent 多任务的工具，而 Task 则是老版本中的名称，不少其他的 Agent 因为历史原因也叫 Task。
3.2 实战——EDA Skill
回到笔者正在开发的 EDA Skill，这个“并行 + 串行”的 Agentic Workflow 设计遵循了上述原则：
[Image]
你可以在Claude Code、OpenClaw 把下面的文字发送给 Agent：
/skill-creator 我想设计并实现一组通用的 CSV/Excel 数据源的 Exploratory Data Analysis Skill。Skill 用英文撰写。

# Steps
- 通过 Code-Act Loop（短期 Planning → 执行 → 观察 → 迭代）驱动分析过程，而不是固定的 Workflow
- 使用 Pandas / NumPy 做数据处理和统计计算
- 使用 Mermaid 做数据可视化（嵌入 Markdown）
- 最终产出一份 支持 Mermaid 的 Markdown 富文本报告，包含：
  - 数据基本面（规模、类型、缺失值等）
  - 发挥 EDA 和 Code-Act Loop 的特长，根据数据灵活决定内容，而不是固定的版块
  - Fun Facts（数据集中有趣或反直觉的发现）
  - 推荐下一步探查的方向

# 强调多任务
- 为提高效率，分析阶段应显式使用 spawn 这个单词来启发模型启动并行子任务（如单变量分析、双变量分析、数据质量检查、Fun Facts 挖掘等同步推进）。在 SKILL.md 的 Frontmatter 里显式的添加 allowed-tools: Agent Task。 
- 其中，第 2 步是可以用多任务进行的，可以“先并行，再串行，再并行，再串行”的模式。注意并行的任务之间应当尽量的独立不依赖。
- 如果用户开启 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`，则 Agent 之间可以协作。
你猜的没错，这段用于生成 Skill 的 Prompt 也是我先和 Sonnet 4.6 讨论的，然后再将这段 Prompt 发送给 Opus 4.6 通过 skill-creator 生成最终的 Skill。最终生成的 EDA Skill 如下所示：
This content is only supported in a Feishu Docs
其中在 Step 2 里，它强调了：
[Image]
看来我们的 Prompt 已经奏效了！让我们来用著名的 Titanic Dataset 测试一下：
[Image]
完全符合我们的设计，Claude Code 主动 Spawn 出 4 个并行执行的子 Agent，分别用于处理单变量画像分析、双变量相关性分析、异常与离群值检测和趣味洞察挖掘。
这是由 EDA Skill 最终由 Skill 的报告：
This content is only supported in a Feishu Docs

This content is only supported in a Feishu Docs


---
4. 进阶：从 Single-Skill 到 Multi-Skill
前面讨论的并行，子任务的指令都写在同一个 SKILL.md 里。但当某个子任务本身足够复杂——复杂到它有自己的工作流、自己的参考文件、甚至自己的 Code-Act 循环——把它硬塞在主 Skill 中就开始显得拥挤了。
更好的做法是：把复杂子任务独立成一个子 Skill，然后在主 Skill 中通过 spawn 子 Agent 时指定使用哪个 Skill 来执行。
这本质上是 Skill 层面的“分而治之（Conquer and Divide）”。主 Skill 扮演 Orchestrator 的角色，只负责任务分解和结果汇总；每个子 Skill 是一个自包含的专家模块，拥有自己的 SKILL.md、references 目录、甚至 bundled scripts。子 Agent 被 spawn 出来后，加载对应的子 Skill，在自己的上下文里独立完成工作，最后把结果交回主 Agent。
这样做的好处是三重的：
1. 每个子 Skill 可以独立迭代——修改异常检测的逻辑不需要动主 Skill 的任何一行；
2. 子 Skill 可以跨场景复用——同一个 financial-analyzer Skill 既能被 EDA Skill 调用，也能被用户直接触发；
3. 它天然实现了 Progressive-Disclosure——主 Skill 只需要知道子 Skill 的名字和职责，不需要把子 Skill 的完整指令加载进自己的上下文。
以笔者的 EDA Skill 为例。Phase 2 的四个子任务中，“Fun Facts Mining”其实可以做得非常深——它可能需要检测 Zipf 分布、计算 Benford 定律偏离度、搜索历史上的今天与数据中日期的巧合、甚至用一些统计假设检验来验证"反直觉"程度。这些逻辑写在 EDA Skill 里会让主文件膨胀，也会让其他三个子任务的指令被 Fun Facts 的细节所"淹没"。
解法是把它独立成 /mnt/skills/user/fun-facts-miner/SKILL.md：
fun-facts-miner/
├── SKILL.md                     # 主指令：Fun Facts 挖掘工作流
└── references/
    └── statistical-tests.md     # 参考：可用的统计检验方法
主 Skill 里关于 Fun Facts 的指令就只有这几行：
#### Sub-task D: Fun Facts Mining
Spawn a new sub-agent, and use `/fun-facts-miner` to execute.  Pass the following context to the sub-agent:
- The loaded DataFrame (saved as a parquet file at a known path) 
- The data quality summary from Phase 1 
- The target: produce 5–8 fun facts, each citing a specific number

The sub-agent will return a structured Markdown section that can be directly embedded into the final report.
而其他的细节——如用哪些统计检验、怎么判断“反直觉”、输出格式的完整规范——都封装在 fun-facts-miner 的 SKILL.md 中了。
这个模式的关键语法是 use /{skill-name}——它告诉 Agent 运行时在 spawn 子 Agent 时去加载指定的 Skill 文件，而不是使用内联指令。这就像人类团队中的一种分工约定：主管说“这个子项目交给张工，按他自己的 SOP 来做”，而不是“这个子项目交给张工，具体步骤是第一步……第二步……”。
当然，并非所有子任务都值得独立成 Skill。笔者的判断标准是：如果一个子任务的指令超过 100 行，或者它在其他场景中也有独立使用的价值，就值得拆出去。 
不仅如此，如果你使用了 Agent Team，也可以使用上述方法，来提前设计需要哪些 Teammates。当然，你也可以将分工的事情留给 Agent 临时决定，不过这可能需要在运行时始终使用更高级的模型（如 Claude Opus 4.6）。


---

5. 给 Skill 设计者的三条建议
如果你也在设计需要并行执行的 AI Agent Skill，笔者总结了三条实操建议。
第一，在 Skill 的元数据中显式声明并行能力。 在 SKILL.md 的 Frontmatter 里，把 Task、Agent 或你的框架中对应的并行工具列入 allowed-tools。这是给模型的“许可信号”——如果你不显式声明，一些模型有可能不会主动使用并行。在 SKILL.md 中，也要使用“spawn”这样的动作词来触发模型的并行意识。
第二，为每个子任务提供足够具体的指令。 Anthropic 踩过的坑值得所有人引以为戒：模糊的子任务描述会导致重复工作。好的子任务描述应包含四要素——目标（做什么）、边界（不做什么）、输出格式（返回什么）、工具指导（用什么工具）。
第三，设计好 fan-out / fan-in 的接口。 fan-out 是 Lead Agent 如何把任务分发给子 Agent；fan-in 则是子 Agent 如何把结果返回给 Lead Agent。两者都需要明确的数据契约。在笔者的 EDA Skill 中，每个子任务的输出规范是“至少 2 个 Mermaid 图表 + 文字洞察”，Lead Agent 在 Phase 3 会按照固定的报告模板把这些素材组装成最终报告。

This content is only supported in a Feishu Docs


---

6. 结语
[Image]
并行子任务不是银弹。但对于“读”多于“写”、子任务间低耦合、需要多视角探索甚至多 Agent 角色讨论的复杂分析任务来说，它是一个显著的架构杠杆。它同时撬动了三个维度：
- 更快的执行速度
- 更干净的推理环境
- 更丰富的发现空间
Cognition 说“不要搞多 Agent”是对的——他们的语境是代码生成这种高耦合任务。
Anthropic 说“多 Agent 提升了 90%”也是对的——他们的语境是信息检索和综合分析。
理解了“读”与“写”的分界线，你就能在自己的 Agent 架构中做出正确的选择。


---

https://my.feishu.cn/sync/Al7fdhQBqssP3Fb7uNkczV3Cnse