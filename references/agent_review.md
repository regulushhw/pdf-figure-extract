# 脚本批处理与 Agent 看图接口

日常纠错走固定入口的[拖点命令](fixed_tasks.md#拖点修正)：程序画编号和坐标网格，一条 move/add/delete/undo 之后自动重画并更新过程表。需要一次改多个点时再用[inspect/edit/undo](fixed_tasks.md#日常主线一次提取批量改点)，数据与渲染契约仍是本文件。下述prepare/apply保留为历史兼容接口，不作为新任务每个点重新建批次的主线。

需要整根柱/须线及同组上下文时，用固定入口的`view_mode=objects`自动生成整对象原图/回叠图；用返回的空白决定模板和`set_axis`回填一维源像素位置。已有review_job可用`review_candidates`直接接续，不重跑检测；具体边界见[整根图元检查](fixed_tasks.md#区间柱组等需要整根图元时)。

复杂计算由已有提取器完成；Agent 只看异常局部和一部分稳定候选，提交少量坐标决定，再由脚本换算、更新结果和生成叠图。不会在本工具内调用付费模型。实际图像阅读、上下文和工具调用仍会消耗 Agent 额度，节省比例须实测，不能预报。

## 输入和调用

`tools/review_points.py`不替代检测器；将检测结果转换为以下job即可接入。输入只用原图像素坐标（左上原点、x向右、y向下），禁止混用PDF pt、截图缩放坐标与源图坐标。适用点、柱端、误差帽及曲线目标采样位置；一个item是一组有共同身份的读数，roles自定义。

```json
{"schema_version":1,
 "sources":{"image1":{"image":"figure.png","pdf_page":5,"figure":"Fig2"}},
 "panels":{"a":{"source":"image1","bounds":[5,5,195,195],
   "axis":{"kind":"linear","coordinate":"y","ticks":[[20,100],[180,0]],
           "unit":"%","max_residual_px":1}}},
 "items":[{"id":"p2-a-m01-s1","panel":"a",
   "identity":{"object":"basin","scenario":"from legend","month":1},
   "anchor":[80,100],"roi":[60,30,100,175],
   "roles":{"upper":[[80,50]],"lower":[]},
   "flags":["lower missing"],"limitations":["interval meaning requires caption"]}]}
```

刻度为`[像素位置,印刷值]`，至少两处；多刻度残差以源图像素计。当前只支持显式线性轴。身份由原图图例与图注先确认；未解身份限制继续保留，端点可见不等于知道它属于哪种情景。可在item中保留原提取器的provenance/limitations，输出会继承。

```sh
python tools/review_points.py prepare job.json work/review --limit 6
python tools/review_points.py apply work/review/review_<tag>_00000.json decisions.json work/result.json
python tools/review_points.py prepare job.json work/review --resume work/result.json --limit 6
```

`prepare`默认最多给6个对象看图，只打印批次ID、路径、计数。缺失、多候选或带flags的对象进队列；稳定候选默认每20个抽1个，`--sample-every 0`关闭抽查。未入当前批次的对象仍保留在完整结果中。稳定单候选也只叫`automatic_candidate`，不会因为其他点抽查通过就自动升级。若任务要求全点查看，可设`--sample-every 1`。同一结果上的固定队列可用`--offset`分页；每轮apply后用最新result重新从offset=0接续，避免队列收缩造成跳项。

每张PNG左列为未标记原像素，右列为候选叠加，绿色为锚点；u0/l0等为角色首字母及候选下标。完整角色、身份和坐标以批次JSON为准。放大用最近邻，不制造新细节。Agent查看PNG后只回填有把握的点，未决定项留空；需要整图上下文时回看source原图。不要把整份逐点JSON反复加载进上下文。

抽查按面板、角色集合及已有`series/scenario/model/statistic`身份字段分组，每组首个稳定候选及其后每20个抽取一次；接续沿用原job的位置，不随未完成队列收缩而移动。其他身份维度可在job设置`"review_sampling":{"identity_fields":["series","period"]}`，显式指定的字段须在每个item中存在。分组只复用已记录身份，不负责辨认或确认身份。缺失、多候选、重合及flags对象始终优先于普通抽样；身份未解的flags不能为提速而删除。

先用`prepare job.json work/review --plan-only`统计异常数、普通抽样数及分组数，不生成图片或结果文件，再按实际队列生成需要查看的批次。候选仍保留未逐点核验状态；抽查通过不能自动升级整组。

试验选项`--context`在局部图上方加入各面板走势及本批ROI框，减少需要身份上下文时重新打开整图的操作。默认使用面板bounds；若须包含轴题或图例，可在panel显式提供`context_bounds`（须覆盖面板），或`legend_bounds`（同一源图上的图例裁剪范围）。工具不检测、解释或确认图例，无单独图例范围时会注明。概览会缩小，仅用于定位；取点仍用下方最近邻放大的原始像素。概览占用空间，是否减少总复核时间须实测，当前不默认启用。新批次的坐标映射与图片摘要包含新增布局；旧批次不改写，不混用坐标。

## 少量决定替代重复取数

不同角色的自动单候选若相距不超过1源图像素，`prepare`也会标记`coincident_roles`并排入检查。它可能是真实重合，也可能是跨行聚类把两条近邻曲线混成一条；看原像素后分别裁定，不默认共用读数。CLI JSON按UTF-8输出，不依赖Windows本机默认编码。

```json
{"batch_id":"复制prepare返回的ID",
 "reviewer":{"kind":"agent","name":"Codex"},
 "decisions":[
  {"id":"p2-a-m01-s1","role":"upper","action":"accept","candidate_index":0},
  {"id":"p2-a-m01-s1","role":"lower","action":"set",
   "space":"source","pixel":[80,140],"note":"原图可见帽线中心"}
 ]}
```

也可直接提交放大PNG中的位置：`action=set, space=sheet, sheet=PNG文件名, view=raw或marked, pixel=[PNG的x,y]`。脚本按该item的裁剪原点、缩放和像素中心偏移反算源坐标；不要自行心算缩放后再当源坐标提交。像素必须落在本批次该对象展示的裁剪范围内，越界须先扩大ROI重新生成批次。

确实没有读到时用`action=missing, note=具体原因`，生成`explicit_missing`和空值；这是明确缺口，不算定量完成。禁止对另一端自动镜像、把缺值变0或凭数值顺序赋予图例身份。无需重复提交已经决定的点。

`apply`核对job、源图、批次PNG及接续结果摘要；错批次、旧底图、同一角色重复提交、非有限/越界坐标拒绝执行。只修改明确提交的角色，其他候选和身份原样保留。输出包括全清单、自动换算值、修正前记录、Agent身份、原图摘要、标定参数及回叠图。原图和Excel不会被改写；输出路径不得覆盖已有文件。

Agent须查看apply生成的`*_check_*.png`；其`viewed=false`是提醒，不能因生成了图片就声称看过。若仍错位，用result接续生成新批次再校正。对`explicit_missing`重试时先改进源图/检测器或ROI并生成新job，不把它藏入已完成计数。正式写表与保存检查沿用现有流程。

## 柱组定位与面板复核

当实心柱的共同底部清晰且各柱在取样行均可见时，可先在布尔墨迹掩膜上调用`propose_bitmap_points.locate_bar_groups(mask, row=..., left=..., right=..., expected_count=..., min_width=8)`。坐标为源图整数像素，左右边界均包含；参数须按实际柱宽设置。函数返回实际水平墨迹段，不按等距月中心外推，数量不符即报错；不会识别观测/模拟、自动拆分组内系列或补缺。调用方先排除框线、网格、文字，再依据图例与各柱边界确认系列。近零、正负混排和网纹柱不能照搬实心柱的定位方法。

同一面板中柱位、图例与全部端点都能清楚显示时，将该面板作为一个item、各月份/系列作为roles，一批集中核对；仍须逐项提交明确决定并查看回叠。过密或局部难辨时再拆分，批大小按可读性确定，不固定要求每次6点或所有面板一屏。已有清晰原图、坐标与叠图可复用，只有缺少判断信息时再生成额外裁剪。


## 连续曲线的取样与返工边界

按任务约定的时间或概率位置取样，不以像素密集取样冒充原始逐日数据。连接计算值与直接读图值分开标明。

先判断线型和所需产物，复用有效候选；检查图应在实际显示尺寸下清晰，保存前同步检查受影响的说明。


## 检查范围

同一Agent提点再看叠图属于自检。`numeric_roles_proposed`计有数值的候选与已看图读数，`numeric_roles_unresolved`计缺失/多解，`visual_roles_unreviewed`计尚未逐点看图；三者分开，不要求把所有自动候选逐格交给模型，也不把有候选误写成已核。按原提取器适用的批量检查及抽样决定自动数据的使用范围。`reviewed`只指本角色已明确回图决定，不证明情景归属、原论文统计定义或整篇范围正确；自动抽查不能称全量视觉复核。本接口的合成验证见`tests/test_agent_review.py`；真实论文试用结果留在项目过程目录，不能当成所有扫描件的准确率。

## 复用既有保存入口

author的原件预检会先创建计划所列备份目录并保存原始字节，校验同名备份摘要后才继续；备份指向当前原件、同名内容不同或不可写时提前停止，不覆盖已有备份。重试复用摘要一致的备份与原件快照，避免作者引擎运行完才发现目录缺失。

共享入口位于`tools/workbook_save/`：`author.mjs`负责写入与预览，`merge_cells.py`保留原生包，`recalculate.ps1`调用已配置引擎和提交检查，`verify_continuation.py`核对计划、原件快照及保存结果。它们读取进程环境变量`SRDP_TASK_DIR`指定的任务目录（变量名为兼容既有调用保留）；新任务只在该目录准备`A1_修改计划.json`等按编号命名的计划并设置输出/备份路径，按原顺序调用共享工具，不复制四份脚本。新目录不放脚本也能完成写入、保存和预览；未设目录时沿用工具所在目录，不建议把任务输出放进安装包。任务结束后清除该进程变量，避免后续任务误用。具体运行依赖与调用见[工作簿保存](tool_reference.md#共享工作簿保存)。

格式冲突检查已纳入共享`plan_errors`：同一单元格被要求不同最终数字格式时，在写入前失败，不等候生成修改稿再发现。数值/来源核验、并发修改摘要、图片保护与保存后检查不省略。公开回归见`tests/test_workbook_save.py`，包括可选的合成图像工作簿整链保存；这些测试验证保存流程，实际论文的数据仍需对照原文。

## 方法参考

方法参考：[PlotTracer误差线交互设计](https://github.com/katalystnord/plottracer/blob/master/docs/error-bars-design.md)中的锚点与像素测量思路。本工具为独立实现，未复制该项目代码，也未使用镜像端点作为测量值。
