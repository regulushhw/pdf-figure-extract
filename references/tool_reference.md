# 工具参考

本页提供进阶命令和配置示例。第一次使用请从 [README](../README.md) 开始。

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

`tools/review_points.py`把已有脚本的候选读数接成“批量提议→少量局部看图→坐标修正→自动换算与回叠”的循环。支持源图坐标及放大检查图坐标回填、按摘要拦截过期批次、完整清单接续。默认只安排异常与部分稳定候选抽查；抽查通过不会把未看的点升级为已核验，也不会镜像补另一端或将缺值填0。详见[接口与调用示例](agent_review.md)。合成行为测试：`python tests/test_agent_review.py`。它不是新的自动图型检测器，实际看图仍消耗Agent额度。

错点的日常改法是 `python tools/drag_points.py task.json show --part …`，然后 `move` / `add` / `delete` / `undo`。每条命令重画带编号和源像素网格的叠图，并更新该 part 的 `points.xlsx` 与 `points.csv`（过程表，不是交付底稿）。`--data` 按已有线性标定移动一个轴，调用方不必手写像素换算或决策 JSON。小标记用 `zoom`。批量 inspect/edit 仍在，一次最多 12 个操作。合成测试：`python tests/test_drag_points.py`。

## 验证范围

`verify`的`checked_value_types`分别统计契约内的数字、空白、文本和布尔值；检查格数不是有效数值数。`<0.001`等p值文本须显式加入契约，不能因它不是Excel数值类型而省略。`review_points`将不同角色位于同一像素附近的自动候选送回看图；它是检查提示，真正重合仍可保留共同读数。

本包附合成回归样例（数量及范围见 `tests/validation_summary.json`），运行后生成JSON结果，用于检验空检查、覆盖缺失、多面板标记、路径丢失、标准矩形点标记及证据输出等行为。合成测试不是论文独立复核。

检查结果只能说明声明的范围内数据一致；读图是否正确仍需对照原图。

## 相关工具与定位

[UnPlotter](https://github.com/ramcdona/unplotter)支持浏览器内矢量提取、标定和导出；[ChartDetective](https://github.com/m-damien/ChartDetective)支持交互提取与重建图检查。本包尝试把部分步骤组织成可留证的Agent流程；尚无依据声称其他工具都不具备这些能力或本包优于它们。

## 维护与许可

[GitHub仓库](https://github.com/regulushhw/pdf-figure-extract)为统一维护源；本地保留一份使用副本，更新时同步已核对的仓库版本。通用方法集中维护在技能与坑库，项目口径留在调用方既有文件。没有新验证经验就不追加，不为接续再造一套进度文件。

本项目的代码与配套文档采用 [MIT许可证](../LICENSE)。第三方依赖仍遵循各自许可证；输入论文、项目数据及提取结果不因使用本工具而自动获得MIT授权。


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

输入沿用[Agent检查接口](agent_review.md)的`sources`、`panels`与`items`；每个item把`roles`换成`selector`，例如：

```json
{"id":"series-month","panel":"a","identity":{"series":"按原图图例确认"},
 "anchor":[80,100],"roi":[60,30,100,175],
 "selector":{"mode":"gray","range":[0,80]},
 "exclude_boxes":[[60,30,70,40]],"neighbor_px":2,"max_run_px":8}
```

也可设`selector:{"mode":"color","rgb":[255,0,0],"tolerance":20}`。ROI、排除框、锚点和邻列窗口均为原图像素；每个item可使用不同的曲线局部区域。范围与阈值需看掩膜确认，不按论文通用。

检测将同一列每个连续墨迹段独立保留，不把上下两条线平均成一条。目标列无墨迹时，仅在明确的邻列窗口内找最近有墨迹列，左右同距均保留且标记；超过窗口仍缺失，不跨年份补值。厚墨迹、多个候选、邻列及无候选送已有检查队列。单候选仍是未复核候选；交叉后合成一段也可能分不开，不能用候选数量证明身份。此工具提供采样点候选，不是整条曲线自动追踪器。

验证：`python tests/test_bitmap_points.py`，覆盖多轨迹不平均、图例遮罩、灰度/颜色、缺点与邻列、厚墨迹、检查接口接通及原图不变。

### 方法参考

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


`python tests/test_trace_bitmap.py`验证交叉、两侧/中间锚点、尖峰、同代价多解、缺墨、断路及现有检查接口。合成测试不能证明任意真实图表的精度。

### 相关方法

- [降雨自记纸曲线提取研究](https://journals.ametsoc.org/abstract/journals/atot/28/7/2011jtecha1505_1.xml)使用动态规划追踪曲线似然图；支持研究路径式追踪，不证明它适配本项目所有多线扫描件。
- [LineFormer官方仓库](https://github.com/TheJaeLal/LineFormer)将折线提取建模为实例分割；官方说明它输出像素点，轴信息须另取。本工具未集成其模型。
- [LineEX官方仓库](https://github.com/Shiva-sankaran/LineEX)把关键点、图表文字、分组与图例映射分开；可参考这种职责分离，模型效果仍需本地图像验证。

本工具独立实现路径选择与接续接口，未复制上述项目代码或附带模型权重。优先检查分线错误位置，再决定补锚点、线型/线宽特征或实例分割实验；不能通过曲线平滑让误差消失。

## 自动选方法入口（连续位图曲线）

通常先用`python tools/auto_trace_bitmap.py trace_job.json new_output_dir`，输入与`trace_bitmap.py`相同。脚本批量执行按列路径和严格墨迹图路径；两者都不连通时，对灰度目标再试有限暗色遮挡路径，无需逐次换脚本。锚点所在列没有选定墨迹时不再中断整次追踪，该列记为`anchor_without_ink`，其余采样点仍给出候选，缺的位置用拖点补。仍须由Agent先确认图例身份、ROI、标定和锚点；本入口不自动解析整篇论文或读取图例。

顺序与保护：

1. 按列路径有效时优先保留；同时与严格墨迹图路径比较，目标采样点相差超过2源像素则保留分歧候选。
2. 严格图路径允许在一列内沿墨迹上下走，适用于陡坡和尖峰。数值取路径实际碰到的那个原始墨迹段中心，不把各条线平均，不事后平滑。
3. 灰度目标可把少量暗色遮挡像素作为高代价路段。非目标像素比例默认上限15%，可在job中显式设置`max_occlusion_fraction`；暗像素上限由目标灰度上限加30、最多245确定。暗色绕行整个结果标记需复核；这不是恢复了遮挡处真实曲线，也不是误差保证。
4. 白色断口不连，遮挡过多或无连通路就报告缺口。方法选择以适用约束和明确优先级决定，不按“读数看起来合理”打分。方法一致也不构成独立验证；短路径可能追错，仍按图例和原图检查。

输出`routing.json`保留各方法状态、参数、路径、非目标像素及逐点分歧；`review_job.json`直接接原有`review_points prepare/apply`。选中的路径回叠到`route_overlay_*.png`。图搜索每个ROI最多30万源像素，较大区域先分段；不在脚本中调用模型。

合成图用于检验路径选择和缺口处理，真实图表仍需核对原图。

`python tests/test_auto_trace.py`验证路由、尖峰、暗色遮挡标记、白缺口/大遮挡拒绝、遮罩一致性、边界及原图不变与检查接口；测试不证明任意水文图均能读取。
