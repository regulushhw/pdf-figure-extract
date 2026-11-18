# WPD核心接入

这是可选的固定X曲线取点入口。调用外部保存的WPD公开核心，源码须匹配`tools/wpd_sources.json`；不安装、下载或操作WPD界面，不涉及AI Assist。Node可从PATH读取，或通过`FIGURE_NODE`指定。第三方源码不并入本仓库。

## 取得匹配版本

本轮实跑的九个文件与官方仓库提交 [`3a3ecb11606945d0701c8a488777e6861be70056`](https://github.com/automeris-io/WebPlotDigitizer/tree/3a3ecb11606945d0701c8a488777e6861be70056) 对应文件的 Git 对象摘要一致。先取得该版本的官方源码目录，然后运行：

```text
python tools/wpd_source_setup.py 官方源码目录 新输出目录
```

程序一次检查下表九个文件并复制为右列文件名；把返回的 `engine_sources_dir` 填入配置。它不下载源码，输出目录必须是新目录且位于源码和工具目录之外。工具按 `tools/wpd_sources.json` 的 SHA-256 检查原始字节，版本或换行改变导致不一致时会拒绝运行。官方仓库使用 AGPL-3.0；其授权说明见官方仓库，本工具代码的许可不替代第三方许可。

| 官方版本内路径 | 本地文件名 |
|---|---|
| `javascript/core/mathFunctions.js` | `mathFunctions.js` |
| `javascript/core/color.js` | `color.js` |
| `javascript/core/dateConversion.js` | `dateConversion.js` |
| `javascript/core/inputParser.js` | `inputParser.js` |
| `javascript/core/calibration.js` | `calibration.js` |
| `javascript/core/axes/xy.js` | `axes_xy.js` |
| `javascript/core/dataset.js` | `dataset.js` |
| `javascript/core/autoDetection.js` | `autoDetection.js` |
| `javascript/core/curve_detection/customIndependents.js` | `curve_detection_customIndependents.js` |

## 一次准备，接续现有任务

```text
python tools/wpd_extract.py prepare config.json new_output_dir --pdf paper.pdf --gate scope.json --part figure
python tools/run_task.py new_output_dir/task.json trial --part figure
```

同一篇论文混用多种读数器时，传入全文共用的 `scope.json`，并用该图卡的 ID 指定 `--part`。把返回目录中的 `review_job.json` 填入全文 `decisions.json` 对应部件的 `job_file`，方法设为 `review_candidates`。随后从全文准备包试读和复核一次。单图准备包可用于定位与导出，整篇工作簿交付仍以全文范围对账。

随后按现有流程看原图与叠图、approve、run。用`drag_points.py`或批量inspect/edit纠错，完成后：

```text
python tools/wpd_extract.py export new_output_dir/task.json figure current.csv
```

导出包含全部冻结位置、稳定ID、源像素、数值、状态与结果摘要。尚未改点时读取有效run快照，改过点时读取当前结果；不回用引擎旧CSV。它输出过程数据，交付工作簿沿用项目保存入口。

已有输出目录会拒绝重建，直接接续其中task。改变原图、标定、选区或工具代码后用新目录，旧证据保留。失败日志留在输出目录，不自动调参或切换算法。

## 配置

JSON包含`schema_version:1`、`engine_sources_dir`、`source.image`、`plot_bounds:[左,上,右,下]`、`axes.x/y`、`sample_at`及`series`。路径相对配置文件；像素使用原生图左上原点，y向下。

- 每轴：`kind`为`linear`或`log10`，`unit`，两个端点`ticks:[[像素,数值],...]`，未参与拟合的`check_ticks`，可选`max_residual_px`。不支持的轴明确拒绝，未知概率轴不能按线性替代。
- `sample_at`：事先约定的全部X，不能按算法输出缩减。
- 每系列：`id`、`name`、`domain:[最小X,最大X]`、`selector:{color:[R,G,B],distance:RGB距离}`、`mask:{polygons:[[[x,y],...]]}`，可选`curve_width_px`。选区表达的是人工确认的系列位置，不会自动判断图例身份。

`mask.exclude_boxes`可选，格式为`[[左,上,右,下],...]`。矩形须位于plot_bounds内，整数像素边界包含两端；它只从检测选区排除这些像素，不改原图。适合排除已经在原图确认的网格或轴线，不自动识别网格。排除带内的真实曲线墨迹也会丢失，WPD可能插值或不给候选；端点和交叠处须回看来源，不能把算法缺点写成原图缺值。

特殊横轴若只读已印出的刻度，可以明确使用源像素X作辅助坐标，并保存每个固定像素与印刷标签的一一对应表。最终结果通过该表恢复标签；不得把辅助线性像素轴称为线性百分数轴，也不得据此任意插入概率取样。当前导出仍保留辅助X，标签转换须显式完成。

读取、按值改点和叠图使用同一线性/log10转换；独立刻度残差超限即拒绝。单图上限300万原生像素，大页先裁出目标图并保存裁切来源，放大不增加细节。

## 选择依据

原表和可用矢量仍优先。空间或颜色可分离的曲线可试WPD；同色交叉、重叠、坐标轴混入选区时先确认身份。柱顶、帽线与区间使用已有图元方法，不能当普通曲线追踪。

先排除选区里的坐标轴、图例和其他系列，再读点。A1图7b开发样例最初的32点中需要纠正8处；只把PE1选区下边界从源像素106改为104，排除零线后，32点全部达到同一原生像素±2的自检标准，无需移动点位。不同系列分别选引擎的开发路线也已跑通，但该例清理选区更简单。这是事后开发试验，不是盲测或总额度证明。

A2图6初始选区还存在交叉后跟到邻线的问题，101或115候选的旧配置均不能用于判定方法优劣。依据线型与交叉前后轨迹修正系列身份后，共124位置得到116候选。完成逐点自检后为116个约值、8个曲线未覆盖位置；99%处两条黑线按原图分辨率取共同约数，另标其性质。相同口径下，现有方法需补点或改点34处，WPD需2处。35个选区引导点和8个另给的追线锚点均保留在成本记录中；总token和完整耗时仍未知。这是同一Agent、同源的开发比较，不是独立精度或额度证明。

算法无候选仍是未处理缺口，不是原图不可读。多个墨迹被平均后可能只剩一个错误候选；候选数量不能用于自动确认系列或相等关系。虚线间隙的算法插值须与原图直接墨迹读数区分。人工选区、看图、纠错和导出都计入成本，不能用程序秒数替代总耗时。
