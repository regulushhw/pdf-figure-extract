# 矢量曲线时间窗统计

需要年内最高/最低或指定月份曲线均值时，使用 `vector_window_stats`。它计算原图显示的分段直线，不恢复作者逐日数据。仅支持线性坐标、直线和严格共线的退化四边形；Bezier曲线、真实面积四边形和对数轴拒绝处理。

## 调用

沿用 `run_task.py` 的 trial → 看原图与极值叠图 → approve → run。part 的 `method` 为 `vector_window_stats`，`job` 与 `trial_job` 指向下列配置。窗口及系列应在输出前固定，不能因覆盖不足删掉目标位置。

```json
{
  "pdf": "paper.pdf",
  "page": 4,
  "frame": [10, 10, 110, 90],
  "axes": {
    "x": {"kind": "linear", "unit": "elapsed days", "ticks": [[10, 0], [110, 365]], "check_ticks": [[60, 182.5]], "max_residual_px": 0.5},
    "y": {"kind": "linear", "unit": "m3/s", "ticks": [[90, 0], [10, 8]]}
  },
  "series": [{"id": "observed", "name": "Observed", "color": "0,0,0", "width_range": [0.9, 1.1], "line_style": "solid"}],
  "windows": [{"id": "year_1", "label": 1, "identity": {"period": "first year"}, "start": 0, "stop": 365, "statistics": ["min", "max", "mean"]}],
  "prefix": "work/data"
}
```

坐标及线宽使用PDF点。系列按原图确认的颜色、宽度、线型绑定，不按高低或路径顺序命名。日期轴可用日期序数，窗口按真实日历生成，包含闰年。用未参与拟合的刻度检查标定。

## 结果与边界

- 每个系列、窗口、指标都有稳定ID，完整窗口才输出数值。均值按横轴时间积分，不能平均顶点值；竖直墨迹影响显示极值，但不增加时间积分。
- `source_window_uncovered` 表示选定来源曲线未覆盖完整窗口，不补线。`ambiguous_curve` 表示同系列包含相互矛盾的曲线，须纠正身份或来源，不能作为普通来源缺值交付。未选中任何路径会直接报错，也不能落成缺值。
- 重复线段不重复积分；选中路径超出声明面板会拒绝，避免把邻图或图例混入。明确的边框矩形会排除，网格束会拒绝。
- JSON与CSV保留覆盖比例和状态。极值附原PDF坐标，均值是派生量，没有伪造的单点坐标。`_check.png` 标出极值供核对；生成叠图不等于已审核。
- 表中注明“图示曲线期间统计，约值”。均值、极值均受原图分辨率、线宽及日期标定限制，不使用作者原始日数据的精度表述。

本模块属于能力维护。用开发例补上模块后再次成功，不得回写为该例首次使用固定工具成功。
