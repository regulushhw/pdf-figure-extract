# 固定任务入口

只有PDF时先用[任务生成器](task_preparation.md)，自动准备图像/相关文字和参数模板；已有有效task直接接续本页。

已有检测器输出的`review_job.json`时，method可用`review_candidates`，job/trial_job直接指向该文件。它校验原图、标定、ID及候选范围，生成定位概览，再接同一个inspect/edit/undo；不重新运行提取器，不继承历史的已核验声明。来源和配置仍受摘要保护。

日常过程只填写JSON、运行入口和看图；新增能力另作维护。入口复用原工具，不会自动解释图例、校准任意坐标或决定研究取样。其完成状态只覆盖声明的任务，不表示整篇论文已完成。

## 日常主线：一次提取，批量改点

使用下文serve保持一个worker。trial→实际看图→approve→run之后，所有位图检测器统一使用inspect/edit/undo，再accept和保存；旧review/apply仅供历史契约接续。

```json
{"action":"inspect","part":"panel_a","points":[{"id":"curve-120","role":"value"}]}
```

省points时默认挑最多6个异常点位，无异常时给稳定候选。返回局部原图/回叠图、稳定ID、当前坐标/值、revision和image_hashes，不必另读全量结果。可附regions查看目标区域：`[{"panel":"a","bounds":[100,50,160,110]}]`，单区域边长最多180源像素；一次最多12个局部视图。坐标始终为原图像素，局部图有源坐标刻度。

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

固定方法名：`vector_bars`、`vector_lines`、`vector_points`、`vector_dashes`、`bitmap_points`、`bitmap_lines`。支持范围与原脚本相同；对数轴或位图虚线等未覆盖能力不能改名套用。返回unsupported/incomplete时修配置、选择已有适用方法或转维护，不能删除系列让检查通过。

试验方法新增`bitmap_bar_ranges`（柱体/横帽/竖线配对）与`bitmap_curve_identity`（线宽、斜率和有界断口多假设）。都输出原复核契约，详见[复杂图元模块](complex_geometry.md)；不表示已支持任意虚线或自动完成图义判断。

## 固定动作

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
| 查看点/区域 | `{"action":"inspect","part":"panel_a"}` | 主流程；返回当前局部图、坐标、revision及图片摘要 |
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

```json
{"export":"export.csv","review_image":"axes_and_curves.png",
 "columns":{"x":"X","series":{"Observed":"Y1","Simulated":"Y2"}},
 "sample_at":[70,75,80,85,90,95]}
```

CSV必须是UTF-8、逗号分隔、单层列名；columns中的系列与scope.series完全一致。X序列必须与sample_at一致，空值保留，非有限数和重复X拒绝；不能把缺列或缺点填0。标定与图例身份仍由Agent看原图确认。合成测试只验证导入、缺值及范围拦截，尚不能证明真实第三方工具的精度或节省额度。

## 缓存与失败边界

缓存绑定来源内容、参数、系列/取样声明和工具版本。来源/参数变化后旧确认失效；已生成文件被改写或丢失会停止，不静默重建证据。执行失败保留日志及不完整目录，修参数后产生新摘要目录；不自动扩大容差或轮流跑所有工具。完整输出留在过程目录，对话只接收短摘要与所需图片。

验证：`python -X utf8 -B tests/test_run_task.py`；配置表格引擎并设置`FIGURE_SAVE_INTEGRATION=1`后额外运行合成整链保存。Windows测试实际检查另一进程的写入被拒绝及退出后的锁释放。上述测试不等于真实论文精度测试，也不测量模型token。
