# pdf-figure-extract

**状态：实验性Skill，持续改进中。** 支持范围和验证边界见下文；不保证任意论文都可自动提取。

面向Agent的论文图表数值化工具：提取数值，保存标定与出处，生成校核叠加图，并检查Excel写入是否一致。执行入口见 [SKILL.md](SKILL.md)。

## 当前支持与限制

- 折线可用显式颜色、线宽与线型绑定同色系列；网格束拒绝、面板边框排除。单条填充四边形虚线另有专用读取器，仍须显式选择；线型不能自动证明情景身份。
- 盘点PDF候选对象；读取部分矢量柱图、直线段曲线和对称点标记。`scan_pages`区分全部`drawing_count`与筛选后的`long_paths`，报告位图原始尺寸、显示分辨率估计和覆盖率；空长路径清单不表示无矢量，页面有位图也不能排除混合图。数字附近的控制字符会提示回看印刷符号，不自动改负号。显示分辨率按轴对齐包围框估计，旋转/变形图片只作参考。
- 自动标定目前限等距线性刻度；对数轴、Bezier路径、堆叠/浮动柱和复杂位图需另用方法。
- 输出保留原数据字段，并增加统一`_evidence`，包括PDF摘要、页面、方法、标定和逐点坐标。原文图号与研究条件由job明确提供，工具不能自行理解论文条件。
- `build_workbook.py`是版式辅助函数；它不会自动生成全部说明、依据或完成视觉检查。
- `verify.py`核对声明数值区与JSON是否一致，不证明原图读得正确。两项工具同时只读检查整本工作簿的公式缓存，拒绝空缓存或Excel错误；缓存可读不证明运算正确或结果最新。检查一次完整覆盖，另做原图检查和保存预览，不用重复运行冒充独立核验。`audit`的`inventory`按原图清单发现整条曲线遗漏、身份错配、重复与点数不足；未列入契约的系列仍不会自动被发现。

## 运行

日常提取新增[固定任务入口](references/fixed_tasks.md)：`python tools/run_task.py task.json serve`。沿用下述提取、复核及保存工具，增加试读确认、结果缓存、过期输入拦截和简短输出；Windows持续运行时锁住现有工具脚本的写入与删除。旧命令仍可用于维护调试。

第三方通过明确列映射接收UnPlotter、ChartDetective、WebPlotDigitizer的CSV导出，不复制其源码；目前只验证了合成导入契约，未声称自动操作过它们的界面或完成真实工具间精度比较。不访问网络、不调用模型、不增加付费API；入口日志统计脚本运行与缓存，不等于实际模型token计量。

安装依赖见 `requirements.txt`。在调用方指定的工作目录运行：

```sh
python /path/to/tools/scan_pages.py paper.pdf --json scan.json
python /path/to/tools/extract_bars.py job.json
python /path/to/tools/verify.py workbook.xlsx checks.json
python /path/to/tools/audit_workbook.py workbook.xlsx audit.json
python /path/to/tests/test_regressions.py
```

柱图示例见 `examples/job_bars.example.json`；实际槽位边界、图例映射、单位和时段须逐图确认。输出前缀指向工作区，原始PDF只读；已有文件修改前备份。

## 共享工作簿保存

`tools/workbook_save/`提供可选的计划写入、原生包保留、引擎检查与保存入口；不包含论文、项目底稿或本机依赖。Python使用requirements中的依赖；写入/渲染另需已配置的`@oai/artifact-tool`与Node.js，提交入口需PowerShell。Codex中先获取工作区依赖路径；普通Python环境不保证具有此表格引擎，可独立使用原有提取和核验工具。

运行时可设置`FIGURE_NODE`、`FIGURE_PYTHON`指定可执行文件，`FIGURE_ARTIFACT_NODE_MODULES`指定已有模块目录；不修改依赖目录，也不将模块打包进仓库。Node会先尝试正常模块解析，再尝试指定目录或运行时相邻的node_modules。

每个任务目录放一份`A1_修改计划.json`（编号可换），至少声明`paper`、`target`、`backup`、当前原件`input_sha256`及`changes`（sheet/range/二维values）；`target`、`backup`使用绝对路径，备份父目录须存在。`styles`可显式设置`from_sheet`与`from`复用原件格式；预览通过`previews`显式指定sheet/range，不猜测业务工作表。调用前先备份并核对计划，以下node/python须为已配置的运行时：

```powershell
$env:SRDP_TASK_DIR = (Resolve-Path -LiteralPath 'work').Path
node tools/workbook_save/author.mjs author 1
python tools/workbook_save/merge_cells.py 1
node tools/workbook_save/author.mjs preview_draft 1
# 查看修改稿后再保存；失败须停止，不能继续提交。
& ./tools/workbook_save/recalculate.ps1 -PaperIds @(1) -BundledEngine
node tools/workbook_save/author.mjs preview 1
Remove-Item Env:SRDP_TASK_DIR
```

请逐条检查退出码；工具失败不代表下一条命令也会自动停止。引擎路径会整本导出并比较全部类型化值，然后保留原生修改包，避免引擎改变空值或图片结构；当前这种路径遇任意公式即拒绝，不能冒充含公式工作簿的重算方案。含公式需适用的原生表格引擎并完成缓存与计算核验。候选提交前后检查原件摘要、计划外内容、工作表与图片，保留替换前文件。

`python tests/test_workbook_save.py`运行合成保护检查；配置好Node、模块目录和PowerShell后，设置`FIGURE_SAVE_INTEGRATION=1`可额外运行无复制脚本的新目录整链测试，输入仅为测试生成的工作簿和色块图片。

## 同色曲线与整条遗漏检查

折线job可用以下字段替换`color_map`（二者互斥）。颜色、线宽和线型须与原图图例核对，不能依数值大小猜情景；线宽单位为PDF pt。

```json
{"series_styles":[
 {"name":"control","color":"0,0,0","width_range":[0.39,0.41],"line_style":"solid"},
 {"name":"future","color":"0,0,0","width_range":[0.79,0.81],"line_style":"dashed"}
]}
```

以下是`audit.json`中的一条规则示例。A列存系列身份，B/C列存两个读数；清单必须先从原图建立，不能只列底稿已有的系列。实际可组合多个身份列，如对象、情景、指标、出处。数据行范围须覆盖完整目标区；分区表头只在无数值且不匹配预期键时跳过。横轴顺序另用`sequence`，数值来源另用`source_records`检查。

```json
{"rules":[{"kind":"inventory","sheet":"Data","row_range":[2,10],
 "key_columns":["A"],"value_columns":["B","C"],
 "expected":[{"key":["control"],"count":2},{"key":["future"],"count":2}],
 "source":"original PDF p5 Fig2 legend and two-point axis"}]}
```

## 填充短块虚线

`python tools/extract_dashes.py job.json`读取预先选定的一条虚线：支持小四边形/矩形填充块及可选的短描边，按中心线读取，在明确上限内做局部断口插值。示例见`examples/job_dashes.example.json`。参数长度均为PDF pt；小块长度、厚度、间隙及端点近邻阈值需按原图选择。`cap_width`仅在确认短描边确属该虚线时开启。

它不会自动分离多条同色虚线，也不读取位图。面板须排除图例、文字与其他同色碎片；离散片段之间缺口过大则保留缺值。近邻端点值会单独标记，不能称为直接读取目标位置。输出JSON保留片段、对象序号、逐点方法、来源摘要和校核叠图；零线附近读数不自动改0，近零裁定由原图复查留据。

## Agent看图接口

`tools/review_points.py`把已有脚本的候选读数接成“批量提议→少量局部看图→坐标修正→自动换算与回叠”的循环。支持源图坐标及放大检查图坐标回填、按摘要拦截过期批次、完整清单接续。默认只安排异常与部分稳定候选抽查；抽查通过不会把未看的点升级为已核验，也不会镜像补另一端或将缺值填0。详见[接口与调用示例](references/agent_review.md)。合成行为测试：`python tests/test_agent_review.py`。它不是新的自动图型检测器，实际看图仍消耗Agent额度。

## 验证范围

`verify`的`checked_value_types`分别统计契约内的数字、空白、文本和布尔值；检查格数不是有效数值数。`<0.001`等p值文本须显式加入契约，不能因它不是Excel数值类型而省略。`review_points`将不同角色位于同一像素附近的自动候选送回看图；它是检查提示，真正重合仍可保留共同读数。

本包附合成回归样例（数量及范围见 `tests/validation_summary.json`），运行后生成JSON结果，用于检验空检查、覆盖缺失、多面板标记、路径丢失、标准矩形点标记及证据输出等行为。合成测试不是论文独立复核。

早期本地A6图7回归曾得到233个数值，与旧摘录在0.6容差内一致；后续回看原图发现旧摘录另有描边柱漏取、缺柱误填0及面板身份错误。现已增加描边识别与独立内容审查契约。这也说明与旧摘录一致不能证明原文读取正确；本包不宣称整张图已完整核验。本仓库不上传论文PDF、项目底稿或逐点项目数据，详细案例记录保留在本地。原先“792个读数”的自述未在本轮复核。

## 相关工具与定位

[UnPlotter](https://github.com/ramcdona/unplotter)支持浏览器内矢量提取、标定和导出；[ChartDetective](https://github.com/m-damien/ChartDetective)支持交互提取与重建图检查。本包尝试把部分步骤组织成可留证的Agent流程；尚无依据声称其他工具都不具备这些能力或本包优于它们。

## 维护与许可

[GitHub仓库](https://github.com/regulushhw/pdf-figure-extract)为统一维护源；本地保留一份使用副本，更新时同步已核对的仓库版本。通用方法集中维护在技能与坑库，项目口径留在调用方既有文件。没有新验证经验就不追加，不为接续再造一套进度文件。

本项目的代码与配套文档采用 [MIT许可证](LICENSE)。第三方依赖仍遵循各自许可证；输入论文、项目数据及提取结果不因使用本工具而自动获得MIT授权。


## 原始读数与取样保护

`audit_workbook.py`新增两种显式规则（旧契约保持兼容）：

```json
{"rules":[
 {"kind":"sampling","sheet":"Data","range":"B1:D1","expected":[1980,1990,2000],"source":"用户确认的取样范围"},
 {"kind":"extraction_records","sheet":"Data","range":"B2:D2","json":"raw.json","sha256":"原始JSON文件的SHA-256","source":"提取前确认的图表范围"}
]}
```

`raw.json`为逐格记录列表，例如：

```json
[{"sheet":"Data","cell":"B2","raw_value":-1.29,"method":"direct","source":"PDF p9 Fig7 / 原始取点坐标"}]
```

例中单条记录只说明格式；实际列表须覆盖规则整个范围，包括缺值。

- `direct`/`neighbor`：底稿必须与`raw_value`一致，禁止静默替换。
- `missing`：原始值和底稿均为空。
- `interpolated`：原始值须为空，另有`calculation:{"value":数值,"inputs":[输入位置或数值]}`；底稿与计算结果一致。工具检查记录完整性，不推断插值公式。
- `reviewed`：允许纠正原始值，但须留`review:{"source":"原图位置","pixel":[x,y],"value":重读值}`；不覆盖`raw_value`。

保留取点时固定文件摘要，不从最终底稿重建原始记录。`sampling`检查约定坐标与实际表头逐项一致；结合`inventory`核对每个系列数值覆盖。机器不会验证用户是否实际同意范围，也不能证明坐标或原始取点正确。未声明这些规则的旧契约不会获得新增保护。

验证：`python tests/test_extraction_guard.py`，覆盖真实突变、非法替换、缺值与插值、坐标重读、错年、摘要变化及记录漏检；是合成行为验证，不是论文识别精度测试。

## 位图批量候选 → Agent局部裁定

`python tools/propose_bitmap_points.py bitmap_job.json new_output_dir`生成`review_job.json`、掩膜和红色掩膜叠图；再用`review_points.py prepare/apply`看图、选候选或修坐标。无新增依赖，不调用模型。输出目录须不存在，原图只读。

输入沿用[Agent检查接口](references/agent_review.md)的`sources`、`panels`与`items`；每个item把`roles`换成`selector`，例如：

```json
{"id":"series-month","panel":"a","identity":{"series":"按原图图例确认"},
 "anchor":[80,100],"roi":[60,30,100,175],
 "selector":{"mode":"gray","range":[0,80]},
 "exclude_boxes":[[60,30,70,40]],"neighbor_px":2,"max_run_px":8}
```

也可设`selector:{"mode":"color","rgb":[255,0,0],"tolerance":20}`。ROI、排除框、锚点和邻列窗口均为原图像素；每个item可使用不同的曲线局部区域。范围与阈值需看掩膜确认，不按论文通用。

检测将同一列每个连续墨迹段独立保留，不把上下两条线平均成一条。目标列无墨迹时，仅在明确的邻列窗口内找最近有墨迹列，左右同距均保留且标记；超过窗口仍缺失，不跨年份补值。厚墨迹、多个候选、邻列及无候选送已有检查队列。单候选仍是未复核候选；交叉后合成一段也可能分不开，不能用候选数量证明身份。此工具提供采样点候选，不是整条曲线自动追踪器。

验证：`python tests/test_bitmap_points.py`，覆盖多轨迹不平均、图例遮罩、灰度/颜色、缺点与邻列、厚墨迹、检查接口接通及原图不变。

### 本轮参考与取舍

- [PlotTracer README](https://github.com/katalystnord/plottracer/blob/master/README.md)：参考ROI限定、掩膜预览及anchor/interpolated区分；该仓库标AGPL-3.0，本包未复制其代码。
- [scientific-plot-digitizer脚本](https://github.com/huangjn58/scientific-plot-digitizer/blob/main/scientific-plot-digitizer/scripts/digitize_plot.py)与[指南](https://github.com/huangjn58/scientific-plot-digitizer/blob/main/scientific-plot-digitizer/references/extraction-guide.md)：参考颜色/灰度筛选、局部ROI与叠图检查。其group_line按整列像素取统计量，未区分同列多轨迹；本包不采用这种合并方式。该指南也把重叠同色曲线列为交互处理情形。
- [chart-data-extractor skill](https://github.com/mohitagw15856/pm-claude-skills/blob/main/skills/chart-data-extractor/SKILL.md)：已审读身份、歧义与输出组织；其特定模型分辨率和可靠性自述未独立验证，不纳入能力承诺，也不引入强制七段报告。

以上为公开方法与接口设计的参考，本工具独立实现；没有据此宣称识别精度超过这些项目。

## 交叉曲线：带锚点的路径提议

`python tools/trace_bitmap.py trace_job.json new_output_dir`在保留的墨迹段上做前向/后向动态规划，约束路径经过已确认锚点；输出逐列像素、全部路径依据、回叠PNG及可交给`review_points`的采样候选。不使用模型，也不把结果自动称为已核。图例身份、校准及原图检查仍由调用方负责。

输入的`sources`、`panels`沿用现有检查接口，新增`traces`列表，例如：

```json
{"sources":{"s":{"image":"figure.png"}},
 "panels":{"a":{"source":"s","bounds":[1,1,108,108],
 "axis":{"coordinate":"y","ticks":[[10,100],[90,0]],"unit":"%"}}},
 "traces":[{"id":"target","panel":"a","identity":{"series":"按原图确认"},
 "roi":[5,5,100,100],"selector":{"mode":"gray","range":[0,80]},
 "anchors":[[10,10],[90,90]],"max_step_px":2,"anchor_tolerance_px":2,
 "ambiguity_cost":0.5,"sample_x":[10,30,50,70,90]}]}
```

锚点须来自看图，x递增；首尾锚点限定追踪区间，内部锚点可选分支。`max_step_px`是每列允许的跳幅，不是结果误差；不得为迎合既有数值而放宽。代价按保留墨迹中心之间的像素跳动累计，只选择路径、不对读数做事后平滑。

同代价/近代价分支保留多候选，代价差不是概率；多墨迹、厚墨迹和分支数变化继续排查。尖峰旁若还有平缓轨迹，最短路径可能选错，须在真实尖峰处加锚点，不能把短路径当真值。没有墨迹的列保留缺值；间断后无两侧局部锚点的段不自动赋予身份。此实现尚不跨缺口连接虚线，也不处理非单值x轨迹。同线长距离完全重合只能记录同一可见轨迹，不能恢复被遮挡细节。

真实试读：本地A7第9页图7 RHUM局部（x=820–850）在当前灰度126–160与每列18px约束下返回`disconnected_path`。这只是当前方法/参数失败，不证明所有方法无解；该图未因此改写底稿或升级核验状态。

`python tests/test_trace_bitmap.py`验证交叉、两侧/中间锚点、尖峰、同代价多解、缺墨、断路及现有检查接口。合成已知几何通过不代表A7整图已解决。

### 后续方法选择的研究依据

- [降雨自记纸曲线提取研究](https://journals.ametsoc.org/abstract/journals/atot/28/7/2011jtecha1505_1.xml)使用动态规划追踪曲线似然图；支持研究路径式追踪，不证明它适配本项目所有多线扫描件。
- [LineFormer官方仓库](https://github.com/TheJaeLal/LineFormer)将折线提取建模为实例分割；官方说明它输出像素点，轴信息须另取。当前未安装其依赖、未下载权重或在A7实测，不报告其对A7的准确率。
- [LineEX官方仓库](https://github.com/Shiva-sankaran/LineEX)把关键点、图表文字、分组与图例映射分开；可参考这种职责分离，模型效果仍需本地图像验证。

本工具独立实现路径选择与接续接口，未复制上述项目代码或附带模型权重。优先检查分线错误位置，再决定补锚点、线型/线宽特征或实例分割实验；不能通过曲线平滑让误差消失。

## 自动选方法入口（连续位图曲线）

通常先用`python tools/auto_trace_bitmap.py trace_job.json new_output_dir`，输入与`trace_bitmap.py`相同。脚本批量执行按列路径和严格墨迹图路径；两者都不连通时，对灰度目标再试有限暗色遮挡路径，无需逐次换脚本。仍须由Agent先确认图例身份、ROI、标定和锚点；本入口不自动解析整篇论文或读取图例。

顺序与保护：

1. 按列路径有效时优先保留；同时与严格墨迹图路径比较，目标采样点相差超过2源像素则保留分歧候选。
2. 严格图路径允许在一列内沿墨迹上下走，适用于陡坡和尖峰。数值取路径实际碰到的那个原始墨迹段中心，不把各条线平均，不事后平滑。
3. 灰度目标可把少量暗色遮挡像素作为高代价路段。非目标像素比例默认上限15%，可在job中显式设置`max_occlusion_fraction`；暗像素上限由目标灰度上限加30、最多245确定。暗色绕行整个结果标记需复核；这不是恢复了遮挡处真实曲线，也不是误差保证。
4. 白色断口不连，遮挡过多或无连通路就报告缺口。方法选择以适用约束和明确优先级决定，不按“读数看起来合理”打分。方法一致也不构成独立验证；短路径可能追错，仍按图例和原图检查。

输出`routing.json`保留各方法状态、参数、路径、非目标像素及逐点分歧；`review_job.json`直接接原有`review_points prepare/apply`。选中的路径回叠到`route_overlay_*.png`。图搜索每个ROI最多30万源像素，较大区域先分段；不在脚本中调用模型。

实测范围（2026-10-20）：5张已知几何合成图，干净线/尖峰/暗线遮挡自动选出对应可行方法，15个采样值最大偏差1源像素；白色断口与大段遮挡未强行补线。另在A7第9页图7 RHUM局部x820–850试用，前两法不通，暗色遮挡路径接通，88个路径像素中3个不在原灰度筛选范围，7个采样位置有候选；已看局部回叠，未改A7底稿，也无独立真值精度评估。单个成功局部不能推出整图覆盖率。

`python tests/test_auto_trace.py`验证路由、尖峰、暗色遮挡标记、白缺口/大遮挡拒绝、遮罩一致性、边界及原图不变与检查接口；测试不证明任意水文图均能读取。
