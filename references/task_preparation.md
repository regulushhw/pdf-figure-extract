# 输入PDF，准备固定任务

```text
python tools/prepare_task.py paper.pdf
```

只接收返回的短摘要，按需看图与局部记录，不加载整份候选/读数。项目有`00_项目入口.md`时默认写入`90_历史归档/Agent生成过程/PDF任务准备/论文名_摘要/`，否则放当前目录`figure_tasks/`；可用`--out 过程目录`指定。不修改PDF，不写工具仓库。同来源、准备工具版本及已有证据复用；输入变化或文件缺失拒绝覆盖，另选过程目录。

`review.md`是短清单；`inventory.json`包含逐页对象、原图片尺寸/位置、图注候选及正文提及片段；`decisions.json`是唯一可编辑配置。坐标分别是PDF pt、页面预览像素、原图片像素，不能混用。文字稀少页明确标记，选图后按需调用[文字识别与标定辅助](ocr_calibration.md)，不默认对整篇所有页面跑OCR。候选不是已确认完整图表目录；无标点图注、复杂排版仍需回图。先查补充材料、原表及同条件正文数值。

## 选择模板，仅填关键字段

```text
python tools/prepare_task.py configure 过程目录 --part 候选ID --method bitmap_points
```

configure先按摘要备份决定文件，不覆盖已有参数。然后Agent编辑JSON数据，不另写配置生成程序：

- action默认pending；extract进入任务；reference表示有依据的参考项，defer表示未完成，后两者必须写note。保留全部候选，不以移除清单缩小范围。
- scope.series/scope.sampling确认全部系列及实际取样；conditions记录指标、单位、基准/目标期与统计量，note留冲突。沿用用户已定范围，不默认缩成代表点。
- vector_lines模板：frame、x_ticks/y_ticks（现有矢量模块至少3个等距刻度）、sample_at、color_map或series_styles。程序不按颜色顺序猜身份。
- bitmap_points模板：image/bounds、x_ticks/x_unit/y_ticks/unit、sample_at、series的name/selector。程序批量展开各系列取样位置，输出墨迹段中心候选；**不能把柱身中心当柱顶或误差线端点**。系列、多轨迹及重叠仍须复核。
- 刻度与图例文字可用`ocr 过程目录 --part ID --image 原图 --bounds 左 上 右 下`生成带位置候选；看图后`ocr-apply 过程目录 --part ID --decision 确认.json`回填标定。原始识别与纠错理由保留；未确认的系列映射、取样和线型参数仍须补齐。
- 其他固定方法用已有job和可选trial_job对象，路径相对过程目录。未提供trial_job会整张小图试读并缓存；大图应提供覆盖前中后、端点及歧义处的小范围trial_job。不声称全部图型已有简化模板。
- 可选顶层workbook沿用plan/checks/audit文件路径，保存仍走已有模块，不猜Excel布局。

## 旧配置登记一次

```text
python tools/prepare_task.py paper.pdf --out 过程目录 --reuse 旧task.json
python tools/prepare_task.py configure 过程目录 --part 导入的ID
```

只导入内容相同PDF的参数、范围和原图引用，默认pending，不继承视觉确认或已核验状态。源图片/导出变化拒绝编译。以后同PDF和输出位置直接复用，不必再次指定reuse；不会扫描所有历史目录寻找配置。configure对已有job省略method即可选择。

## 一次试读、一次摘要

```text
python tools/prepare_task.py trial 过程目录
python tools/prepare_task.py status 过程目录
```

trial自动编译所选项、调用现有run_task，统一返回检查图、失败原因和执行/缓存统计；只生成任务用build。参数变化生成新built目录，保留旧证据。候选与未选项保存在scope.json，不把所选范围冒充整篇完成。

后续用返回task路径接[固定入口](fixed_tasks.md)的approve/run/inspect/edit/undo/accept/draft/save。只准备一次，纠错只更新点数据及局部图；生成器不自动确认、不写研究底稿。部分失败返回incomplete，不自动换阈值/轮流跑所有工具。修正后仍须看回叠图。

prepare_seconds、operation_seconds分别是准备/当前动作时间；computer_seconds是提取子进程时间；elapsed_since_prepare_seconds_including_wait是从开始准备至今，包含Agent判断、用户等待和空闲，不含此前对话或工具开发。没有测模型token，不声称额度节省比例。

验证：`python -X utf8 -B tests/test_prepare_task.py`。合成PDF/图片验证准备、规则图注、模板、真实提取入口、旧任务导入、缓存及输入变更；不证明任意论文的图义或取值准确。
