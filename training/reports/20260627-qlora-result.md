# QLoRA 训练结果 — 2026-06-27

## 训练配置

| 项目 | 值 |
|------|-----|
| 基座模型 | Qwen2.5-1.5B-Instruct |
| 量化 | 4-bit NF4 (bitsandbytes) |
| LoRA rank | r=16, alpha=32, dropout=0.05 |
| 目标层 | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| 训练数据 | train 720 / validation 160 / test 160 |
| Epochs | 3 |
| Batch size | 4 × 4 grad_accum (effective 16) |
| Learning rate | 2e-4 |

## 评测结果（160 条测试样本）

| 指标 | Zero-shot 基线 | QLoRA Adapter | 提升 |
|------|:-----------:|:----------:|:----:|
| **Accuracy** | 51.88% | **65.00%** | +13.12pp |
| **Macro-F1** | 0.4694 | **0.6492** | +0.1798 |
| 错误数 | 77 / 160 | 56 / 160 | -21 |

## 各类 F1 对比

| 意图 | Zero-shot | Adapter | 变化 |
|------|:---------:|:-------:|:----:|
| product_consultation | 0.6909 | 0.7647 | +0.07 |
| compatibility | 0.3333 | **0.8333** | +0.50 |
| order_status | 0.4138 | 0.4737 | +0.06 |
| shipping | 0.6667 | 0.5500 | -0.12 |
| return_refund | 0.5797 | 0.7500 | +0.17 |
| warranty_fault | 0.5909 | 0.6957 | +0.10 |
| complaint | 0.0800 | **0.6000** | +0.52 |
| human_handoff | 0.4000 | 0.5263 | +0.13 |

## 关键发现

1. **compatibility** 提升最显著 (+0.50 F1)：zero-shot 大量误判为 product_consultation，Adapter 大幅纠正。
2. **complaint** 从几乎不可用 (F1=0.08) 提升到 0.60：zero-shot 几乎全错判为 warranty_fault / return_refund。
3. **shipping** 是唯一下降的类 (-0.12)：Adapter 易与 order_status 混淆，两者语义边界模糊。
4. **order_status** 仍是最弱类 (F1=0.47)：与 shipping/return_refund 三者间混淆严重，可能需要更多训练样本或合并标签。

## 产物位置

| 产物 | 路径 | 提交 |
|------|------|:----:|
| Zero-shot 报告 | `training/runs/qlora-20260627/reports/zero_shot_report.json` | 否 |
| Adapter 报告 | `training/runs/qlora-20260627/reports/adapter_report.json` | 否 |
| LoRA Adapter 权重 | `training/runs/qlora-20260627/checkpoints/` | 否 (本地保存) |
| 训练脚本（云端成功版） | `training/runs/qlora-20260627/train_qlora.py` | 已同步到源码 |
| 压缩包 | `training/runs/qlora-20260627/qlora-results-20260627.tar.gz` | 否 (本地保存) |

## 云端训练耗时 / loss

未记录（训练日志未包含在下载产物中）。

## 备注

- checkpoint（.safetensors / .bin）和 tar.gz 文件不提交 Git，仅本地保存于 `training/runs/`。
- `training/runs/` 目录已加入 .gitignore。
- 报告中的指标均来自 AutoDL 云 GPU 实际运行输出，非编造。
