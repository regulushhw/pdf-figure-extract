# 固定任务入口

只有PDF时先用[任务生成器](task_preparation.md)，自动准备图像/相关文字和参数模板；已有有效task直接接续本页。

已有检测器输出的`review_job.json`时，method可用`review_candidates`，job/trial_job直接指向该文件。它校验原图、标定、ID及候选范围，生成定位概览，再接同一个inspect/edit/undo；不重新运行提取器，不继承历史的已核验声明。来源和配置仍受摘要保护。

日常过程只填写JSON、运行入口和看图；新增能力另作维护。入口复用原工具，不会自动解释图例、校准任意坐标或决定研究取样。其完成状态只覆盖声明的任务，不表示整篇论文已完成。

## 拖点修正

少量错点直接修正，不必重调参数或新写提取代码；成片偏差先核对标定、系列和选区等共同配置，有原图依据时修正配置，再处理剩余错点。程序画出带编号、id 和坐标网格的叠图；看图后每次一条命令。命令结束会重画叠图，并更新该 part 的 `points.xlsx` 与 `points.csv`。这是过程表，不是交付底稿；交付仍走 accept / draft / save。

网格数字是源图像素（左上原点，y 向下），不是 PNG 里的位置。括号里是已标定那一轴的数据值。`--data` 只移动这一轴，另一轴保持原像素。

```text
python tools/drag_points.py task.json show --part panel_a
python tools/drag_points.py task.json zoom --part panel_a 3
python tools/drag_points.py task.json zoom --part panel_a --box 100,40,180,120 --panel a
python tools/drag_points.py task.json move --part panel_a 3 --pixel 120 64
python tools/drag_points.py task.json move --part panel_a 3 --data 12.5
python tools/drag_points.py task.json add --part panel_a new-id --panel a --series 图例系列 --pixel 30 45
python tools/drag_points.py task.json delete --part panel_a 4
python tools/drag_points.py task.json undo --part panel_a
```

图例系列分别记录。只有原图或可靠来源能确认对应点确实重合时，才复制读数；仅因遮挡或分辨率不足无法区分时，记录“未能分辨”，不要复制其他系列：

```text
python tools/drag_points.py task.json copy-series --part panel_a --from "50 mg/L" --to "100 mg/L" --to "150 mg/L"
```

每条图例系列各有一行，坐标与数值相同，叠图标签和 `points.xlsx` / `points.csv` 都写 `coincident_with=` 其余系列。这是同一处读数的重复登记，不是再测一次。

`3` 是叠图编号，也可以写点的 id。编号在增删之后会重排，id 不变；每次返回里都有当前编号对照。move / add 的落点必须在刚看过的叠图内，超出就先 zoom 或 show。zoom 用最近邻放大，边长最多 220 个源像素，用来看清粗标记和误差线。环境科学图大多是扫描或内嵌位图；矢量提取没有路径时改用 `bitmap_points` / `bitmap_lines`（或已有 `review_candidates`）再拖点。锚点列没有选定墨迹时，追踪记为 `anchor_without_ink`，其余采样点仍可拖。

已经开着 serve 时，不要再对同一 work_dir 跑上面的命令。把同样的字段写成一行 JSON 送进 worker，不必附 revision、图像摘要或决策文件：

```json
{"action":"drag","part":"panel_a","command":"move","target":"3","data":12.5}
```

`command` 为 show、zoom、move、add、delete、undo。一次要改多个点时，再用下面的批量接口。

## 日常主线：一次提取，批量改点

使用下文serve保持一个worker。trial→实际看图→approve→run之后，用上面的拖点命令修正；需要一次改最多 12 个点时再用 inspect/edit/undo，然后 accept 和保存。旧review/apply仅供历史契约接续。

```json
{"action":"inspect","part":"panel_a","points":[{"id":"curve-120","role":"value"}]}
```

省points时默认挑最多6个异常点位，无异常时给稳定候选。返回局部原图/回叠图、稳定ID、当前坐标/值、revision和image_hashes，不必另读全量结果。可附regions查看目标区域：`[{"panel":"a","bounds":[100,50,160,110]}]`，单区域边长最多180源像素；一次最多12个局部视图。坐标始终为原图像素，局部图有源坐标刻度。

点位属于同一候选重叠段且可以放进一张局部图时，共用一张原图和叠图，并保留每个点的编号和可编辑范围。叠图用 `c0`、`c1` 等标识备选，组合视图在前面加点号；`source_candidates` 保留完整备选像素。核对后可提交 `{"op":"choose_candidate","id":"curve-120","role":"value","candidate_index":1}` 选中当前局部图里展示的 `c1`，其余编辑规则不变。声明了取样列的缺口按源图 X 坐标打开。

虚线插值点在局部图标出两端原图墨迹，点位 `source_basis` 保存两端坐标。确认同一个落点后，草稿目录的 `bound_readings.json` 将插值依据绑定到目标工作表与单元格。移动到别处的落点重新按人工修正记录。表注注明涉及的插值范围及精度。

边缘取样列仍属于原定表格。查看当前局部图后，已确认的缺口可用 `delete` 操作附 `missing_kind: "source_uncovered"` 或 `"source_unreadable"`，并在操作备注写具体判据。草稿按冻结的单元格绑定写入「—」，在 `bound_missing_evidence.json` 保留原图摘要和本次看到的 ROI。早期人工填写的缺值证据仍可用；两份证据冲突时报错。

看图后填写一个决定文件，批量修改：

```json
{"base_revision":"返回的revision","images":{"检查图绝对路径":"返回的SHA256"},
 "viewed":true,"note":"具体纠错依据",
 "ops":[{"op":"move","id":"curve-120","role":"value","pixel":[120,63.5]},
        {"op":"delete","id":"curve-140","role":"value"},
        {"op":"add","id":"curve-160","role":"value","pixel":[160,76]},
        {"op":"reassign","id":"curve-180","role":"value","series":"原图确认的系列"}]}
```

```json
{"action":"edit","part":"panel_a","batch":"inspect返回的view路径","decision":"edits.json"}
```

一次1—12个操作；move/delete/reassign/accept及补已有缺口的add都须在当前视图中。移动超出已看范围先inspect目标区域；新ID的add须给panel、identity、role和pixel，落在已看区域。accept表示明确看过该点，不是自动全点批准。reassign目前只支持单角色点；多角色区间对象需明确整体身份，不靠改一端替换对象。

更新只改变当前result版本及受影响局部图，原图解码在worker复用，提取次数保持不变。返回新revision和新局部图，可直接作为下一批决定的观察依据。每批保留前后版本，失败整批不生效，旧revision拒绝。删除必需点保留explicit_missing，不能靠删行缩小约定范围；删除编辑新增的额外点会移出当前结果，历史仍在。

撤销上一整批：决定文件`{"base_revision":"当前revision","note":"撤销原因"}`，提交`{"action":"undo","part":"panel_a","decision":"undo.json"}`。撤销也返回当前局部图。所有数值或身份修改使旧accept失效；最终确认必须覆盖当前修改图。未看候选不因邻点纠错变成已核验，同一Agent仍只算自检。

旧apply可读写同一结果，但会清空快速撤销栈，避免undo误撤销别的接口产生的修改；历史版本文件仍保留。正常任务不在两套操作之间来回切换。

operation_seconds计当前动作内的读取、修改、保存与出图，render_seconds单列；不包含Agent判断、工具传输和用户等待。extraction_executions用于核查是否误重跑，不能代替端到端时间。纠错不写研究底稿，保存链保持不变。

### 区间、柱组等需要整根图元时

给inspect加`"view_mode":"objects"`，points明确列出同一月/柱组的端点ID和role（最多12对）。程序合并所选对象在同一面板的原有ROI，自动出带像素尺的原图/回叠图，并用1、2…编号对应返回的points；不会从图像猜月份、系列或图元身份。每面板单独一图，至少保持1:1源像素；范围过宽/高会要求选择较小的既有ROI，不靠缩小整图隐藏细节。跨情景/时期的ROI不宜一次合并。

objects用ROI内连续墨迹和当前候选收紧纵向空白，裁剪仅是显示启发式。返回views保留full_bounds；觉得线被截断或需查看原范围时，改用`view_mode=objects_full`。墨迹收紧不证明图元完整、身份或端点正确；改点仍必须落在实际显示范围内。大原图不额外放大，低分辨率图最多放大3倍，坐标尺始终标源图像素。

```json
{"action":"inspect","part":"month1","view_mode":"objects",
 "points":[{"id":"future2020","role":"uncapped_top"},
           {"id":"future2020","role":"uncapped_bottom"}]}
```

返回`decision_template`：绑定当前revision和图像摘要，默认viewed=false、note为空、坐标为空。实际查看后填写决定；纵轴读数可用`{"op":"set_axis","id":"future2020","role":"uncapped_top","position":302}`，position是**源图像素纵坐标**，不是数值百分比。横轴标定时则是横坐标。程序保留另一坐标（当前点或原对象anchor）、按既有标定换算；另一坐标本身有误时用move同时更正两维。看不清时用delete并写具体原因，必需端点仍以缺值保留。

模板可只保留本轮已判断的操作。未填模板不能执行，也不会接受原候选；当前图外的改点会被拒绝。edit自动沿用对象视图回叠，undo仍回到点视图并保留历史。这里固化的是裁图、坐标回填和换算流程，不是自动识别重叠、网纹内部或遮挡端点的能力。

## 配置一次，按阶段接续

既有xlsx续补使用`tools/workbook_save`时，先把同址修改收敛到最终值，连续单元格整块写入；不要逐点重复解析整张表。交付排版可在计划中声明`column_widths: [{"sheet":"数据","min":2,"max":2,"width":26}]`与`merges: [{"sheet":"数据","range":"A40:I40"}]`。只合并除左上角外均为空的说明行；分组列宽会拆分保留未改列的原设置。静态引擎保存时在同次导入中生成`engine_preview`，不必只为同一视图再次导入整本。宽度、合并和原数值/图像保存一起核对；不为改一句说明重新提取原图。

图与表同页时若缩放使图例或刻度看不清，可按项目要求将原图单独分页；单独图页避免重复打印标题行导致图像重复。保存入口保护已有图片，不新增插图或设置打印标题；分页和新增图片由适用的工作簿流程处理，完成后检查实际打印效果。

所有路径以各自JSON所在目录为基准；保存计划的target/backup必须是工具仓库之外的绝对XLSX路径。输出目录必须与工具仓库分开，建议放调用项目的过程目录。原PDF只读。

```json
{
  "version": 1,
  "work_dir": "figure6_run",
  "pdf": "paper.pdf",
  "parts": [
    {
      "id": "panel_a",
      "method": "bitmap_lines",
      "trial_job": "trial.json",
      "job": "full.json",
      "scope": {"series": ["Observed", "Simulated"], "sampling": "用户约定的概率点及全部系列"}
    }
  ],
  "workbook": {"plan": "save_plan.json", "checks": "checks.json", "audit": "audit.json"}
}
```

`trial_job`与`job`沿用原工具输入结构，不再写一份新的提取程序。先在原图上建立系列和取样清单；试读覆盖前、中、后和难辨处。小图可用同一job试读，确认后完整运行会直接复用候选。不同面板/图元使用不同part。需要写表时，在初始任务配置里声明workbook三个文件的路径，文件内容可在提取后填写、首次draft前完成。纯提取任务可省略workbook；之后改变任务定义须用新过程目录，不覆盖旧证据。

版本 2 的 `draft` 从已核验的 `printed` 和已确认的读图绑定生成数值写入块。同一工作表内连续列归为一块。声明小数位时按该位数写入和显示，只调整绑定数字的显示格式，并逐格验证。明确缺值沿用现有证据关卡。计划仍需声明目标簿、备份、预览及检查文件；`changes` 可只写补充的排版文字。手填写入若与绑定读数或缺值相冲突，生成草稿前即报错。

固定方法名：`vector_bars`、`vector_lines`、`vector_points`、`vector_dashes`、`bitmap_points`、`bitmap_lines` 和 `vector_window_stats`。各方法支持范围以其实际说明为准，不能改名套用；例如 [WPD核心入口](wpd_core.md)支持线性和log10轴，[时间窗统计](vector_windows.md)有自己的轴和覆盖要求。返回unsupported/incomplete时修配置、选择已有适用方法或转维护，不能删除系列让检查通过。

试验方法新增`bitmap_bar_ranges`（柱体/横帽/竖线配对）与`bitmap_curve_identity`（线宽、斜率和有界断口多假设）。都输出原复核契约，详见[复杂图元模块](complex_geometry.md)；不表示已支持任意虚线或自动完成图义判断。

## 固定动作

冻结表格范围后，运行 `python tools/run_task.py TASK.json coverage`。命令在任务目录原地生成 `coverage.json`，逐格列出原文值、尚未运行、绑定不匹配、数值候选、复核读数、未解缺值和有证据的源图缺值。提取未完成时也能运行，用于定位应补的列与系列。最终交付仍由原有对账关卡检查。

任务配置了目标工作簿时，覆盖报告还会列出原单元格值，并区分横杠已有可复核替代值、横杠只有候选、已有数值与复核结果不一致，以及已有数值对应的采样位置实际没有源图覆盖。核对名单可直接用于同一轮审阅稿。

局部纠错任务可在范围配置中设置 `review_regions: true`，把同一原图在同一工作表的多个不重叠矩形一起核对，再生成一份审阅稿。此模式适用于仅冻结改动范围的任务。整篇基线仍要求各输出表保留独立工作表。

扫描图可直接从表格地址打开当前原图复核，例如 `python tools/run_task.py TASK.json inspect --part PART --cell "图6-3!J6" --cell "图6-3!K6"`。只有当前提取结果中唯一绑定的单元格才会打开复核。当部分出自多个面板或方法时，按提取部分分别检查。

生成审阅稿时，已列入预览计划且带原图的工作表至少有一张预览覆盖其声明的打印区。原范围只覆盖局部时，任务会扩展一张预览，同时保留其余细节预览。多个不相连的打印区应分别指定预览。生成后还需查看实际图像，确认原图渲染出来。

矢量折线的读数证据包含笔画在取样列上的纵向范围、轴标定偏差和换算后的原单位范围。交付表在范围配置中设置 `"precision": "source"` 时，以该证据限制显示小数位。表的 `decimals` 同时存在时取两者中较少的小数位。此设置适用于有逐点测量证据的读数，混合来源表在所有数值具备该证据后再启用。

原图范围较宽时，来源精度可把整数进一步取整到十位或更粗。只有取整结果仍在原图换算范围内才使用，逐格证据保留未取整数值。

矢量采样无交点时，覆盖报告列出采样列、所选路径在面板内的可见范围和最近端点。校核图的橙色标记表示待查采样列，纵向位置借用最近端点以便定位。它不是数据落点。端外、贴近笔画端部和路径内部断点分别标记，仍需原图确认缺值依据后才能入表。

矢量折线可在 `accept` 的复核决定中逐点填写 `vector_gaps`，每项填写 `series`、`x`、`kind` 和 `reason`。确认超出路径且源图未覆盖时使用 `source_uncovered`；核查原图后仍无法分辨时使用 `source_unreadable`。程序自动把未标注的原图、像素框和校验值绑定到单元格。没有逐点确认的缺值不能生成审阅稿。

扫描曲线的逐点测量取原取样列的连续墨迹边界，加上半像素和纵轴标定偏差，再换算到原单位。它描述纵向笔画分辨率。插值点、改到另一处的点和没有观测边界的点没有这份测量证据，需采用相应读数方式的精度依据。

柱顶需有原图像素支持柱体边缘。无帽区间分别取上下端的原始线段终点，有帽区间分别纳入上下帽线厚度。每个角色单独换算范围。多个区间重叠且无法确定归属时不提供单一范围。

候选帽线须从同一线身两侧伸出。程序将端部的横向墨迹与紧邻内侧的线身宽度比较，粗线身本身不会因达到最小帽宽而被判成帽线。

有帽区间的线身可从原图直接定位时，可在 `capped_ranges` 对象中指定 `stem_x_window`，填写取图时确定的左右像素列。区间内仍有多个候选时继续复核；区间外的候选保留在几何证据中。这个区间只约束线身位置，帽线仍按完整裁剪区检测。

局部复核图以 `x1`、`x2` 标出视野内被排除的帽线端点，坐标也随复核结果返回。原始候选列表仍只含符合线身区间的点。复核后可通过移动操作覆盖读数，并记录理由。

推荐启动一个持续运行的worker：

```text
python tools/run_task.py task.json serve
```

随后每行提交一个JSON动作，输入`quit`结束。Windows worker存活期间，现有`.py/.mjs/.ps1`工具文件不能被其他进程写入或删除；进程退出即释放。它不是对Agent其他工具的权限管理，不能阻止在别处编程。非Windows只有摘要检查，需另配操作系统沙箱才有强制写隔离。工具更新后使用新过程目录，旧结果保留。

| 动作 | 输入示例 | 返回/限制 |
|---|---|---|
| 盘点 | `{"action":"scan"}` | PDF对象候选摘要与完整JSON位置；相同来源复用 |
| 试读 | `{"action":"trial","part":"panel_a"}` | fingerprint、检查图和结果目录，仍是未复核候选 |
| 确认试读 | `{"action":"approve","part":"panel_a","decision":"trial_decision.json"}` | 校验当前图片、身份与范围的明确查看声明 |
| 展开 | `{"action":"run","part":"panel_a"}` | 未确认试读拒绝；全量job改变使旧确认失效 |
| 拖点 | `{"action":"drag","part":"panel_a","command":"show"}` | 编号叠图、源像素网格和过程表；move/add/delete/undo 不重跑提取 |
| 查看点/区域 | `{"action":"inspect","part":"panel_a"}` | 批量接口；返回当前局部图、坐标、revision及图片摘要 |
| 批量改点 | `{"action":"edit","part":"panel_a","batch":"view.json","decision":"edits.json"}` | 主流程；只改数据和局部图，不重跑提取 |
| 撤销上一批 | `{"action":"undo","part":"panel_a","decision":"undo.json"}` | 恢复前版并给局部图，保留版本历史 |
| 局部复核 | `{"action":"review","part":"panel_a"}` | 沿用已有队列，默认一次1对象、稳定区每20抽1；重复请求复用未处理批次 |
| 提交修正 | `{"action":"apply","part":"panel_a","batch":"完整批次路径","decision":"points_decision.json"}` | 沿用原review_points决定结构；输出新的结果和回叠图 |
| 确认范围 | `{"action":"accept","part":"panel_a","decision":"full_decision.json"}` | 有已知缺值/未裁定候选拒绝；抽查不称全点核验 |
| 生成修改稿 | `{"action":"draft"}` | 全部part确认后调用共享写入、原生包保留与预览 |
| 保存 | `{"action":"save"}` | 要求预览/说明确认，先核对修改稿，再执行引擎及原件保护检查，保存后再次核对 |
| 只读核验 | `{"action":"verify"}` | 核对当前最终文件，不修改数值 |
| 状态 | `{"action":"status"}` | 阶段、脚本执行次数、缓存命中、子进程秒数；不是token账单 |

同样支持一次性命令，例如`python tools/run_task.py task.json trial --part panel_a`。它只在该命令运行期间持有文件锁；要求整个提取会话保持工具只读时使用serve。配置不支持任意可执行文件、命令字符串、shell片段或动态插件。Node/Python模块等既有运行时仍从可信环境变量配置，不从任务JSON接收。

试读/范围决定结构如下；images使用返回图像的绝对路径和SHA-256，必须覆盖当前该阶段的全部检查图。apply后新增的回叠图也要查看，不能只沿用原候选叠图。完整路径和摘要可从task_state.json定向读取，不必反复加载全部数值。

```json
{"part":"panel_a","fingerprint":"试读或展开返回的值",
 "images":{"检查图绝对路径":"SHA-256"},
 "identity_checked":true,"scope_checked":true,"note":"实际查看范围、取样及保留限制"}
```

这是Agent的明确看图记录，脚本不能证明看图动作实际发生，也不因此成为独立验证。位图先使用review/apply解决多候选/缺口；稳定区未逐点查看的状态仍保留。矢量及第三方导出也要明确accept，不能因脚本退出0就直接保存。

保存前在work_dir放`save_decision.json`：`{"draft_sha256":"修改稿SHA-256","images":{"全部修改稿预览绝对路径":"SHA-256"},"viewed":true,"notes_checked":true}`。先核对受影响的旧缺口文字、单位与出处，不能为减少保存次数留下错误。保存后预览仍须查看；saved_checks_passed不是“整篇已完成”。原有共享保存入口依赖及静态引擎拒绝公式的限制不变。

## 第三方工具导出

先试现有工具；遇缺口再选一个成熟工具。通过独立应用导出数据，保留原图、标定及系列选择的检查图，不把它们的源码并入本仓库：

- [UnPlotter](https://github.com/ramcdona/unplotter)：矢量路径、轴校准、系列选择和CSV/JSON导出。
- [ChartDetective](https://github.com/m-damien/ChartDetective)：复杂矢量图的交互提取和重建检查。
- [WebPlotDigitizer](https://automeris.io/docs/)：图片图表的计算机视觉与人工辅助数字化。

对应方法名为`unplotter_csv`、`chartdetective_csv`、`webplotdigitizer_csv`；只接收显式导出契约，尚不包含这些应用的自动界面操作。原始导出有多层表头或不同分隔符时，应在工具内选择可用导出格式；不猜测表头、区域数字格式或系列归属。

WPD另有可选的[公开核心运行入口](wpd_core.md)：一次准备生成`review_candidates`任务，复用这里的纠错、撤销和接续；它与上述CSV导入不同，不操作应用界面。线性/log10坐标在读取、改点和叠图中共用转换。

```json
{"export":"export.csv","review_image":"axes_and_curves.png",
 "columns":{"x":"X","series":{"Observed":"Y1","Simulated":"Y2"}},
 "sample_at":[70,75,80,85,90,95]}
```

CSV必须是UTF-8、逗号分隔、单层列名；columns中的系列与scope.series完全一致。X序列必须与sample_at一致，空值保留，非有限数和重复X拒绝；不能把缺列或缺点填0。标定与图例身份仍由Agent看原图确认。合成测试只验证导入、缺值及范围拦截，尚不能证明真实第三方工具的精度或节省额度。

## 缓存与失败边界

缓存绑定来源内容、参数、系列/取样声明和工具版本。来源/参数变化后旧确认失效；已生成文件被改写或丢失会停止，不静默重建证据。执行失败保留日志及不完整目录，修参数后产生新摘要目录；不自动扩大容差或轮流跑所有工具。完整输出留在过程目录，对话只接收短摘要与所需图片。

验证：`python -X utf8 -B tests/test_run_task.py`；配置表格引擎并设置`FIGURE_SAVE_INTEGRATION=1`后额外运行合成整链保存。Windows测试实际检查另一进程的写入被拒绝及退出后的锁释放。上述测试不等于真实论文精度测试，也不测量模型token。
