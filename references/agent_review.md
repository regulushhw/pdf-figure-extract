# 脚本批处理与 Agent 看图接口

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

## 少量决定替代重复取数

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

## 检查范围

同一Agent提点再看叠图属于自检。`reviewed`只指本角色已明确回图决定，不证明情景归属、原论文统计定义或整篇范围正确；自动抽查不能称全量视觉复核。本接口的合成验证见`tests/test_agent_review.py`；真实论文试用结果留在项目过程目录，不能当成所有扫描件的准确率。

方法参考：[PlotTracer误差线交互设计](https://github.com/katalystnord/plottracer/blob/master/docs/error-bars-design.md)中的锚点与像素测量思路。本工具为独立实现，未复制该项目代码，也未使用镜像端点作为测量值。
