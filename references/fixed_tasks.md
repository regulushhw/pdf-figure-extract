# 固定任务入口

日常过程只填写JSON、运行入口和看图；新增能力另作维护。入口复用原工具，不会自动解释图例、校准任意坐标或决定研究取样。其完成状态只覆盖声明的任务，不表示整篇论文已完成。

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
