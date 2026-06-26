# 意图分类训练与评测

## 数据

| Split | 样本数 | 说明 |
|-------|--------|------|
| `data/train.jsonl` | 720 (90×8) | 训练集 |
| `data/validation.jsonl` | 160 (20×8) | 验证集 |
| `data/test.jsonl` | 160 (20×8) | 测试集（独立模板，不与 train 近似重复） |

### 八类意图

`product_consultation` | `compatibility` | `order_status` | `shipping` | `return_refund` | `warranty_fault` | `complaint` | `human_handoff`

### 数据校验（本地）

```bash
cd training
source ../backend/.venv/bin/activate
python scripts/validate_data.py
```

## 云 GPU 训练流程

### 0. 前置：环境检测

```bash
# 确认 GPU 可用
nvidia-smi

# 确认 CUDA 版本
nvcc --version

# 确认显存（推荐 ≥16GB）
nvidia-smi --query-gpu=memory.total --format=csv,noheader
```

若 1.5B 模型显存不足，脚本自动回退至 `Qwen2.5-0.5B-Instruct`，并在日志中说明原因。

### 1. 依赖安装

```bash
pip install torch transformers peft bitsandbytes accelerate trl datasets tqdm numpy
```

### 2. 上传代码与数据

将 `training/` 目录完整上传至云 GPU 实例：

```bash
scp -r training/ user@your-gpu-instance:/home/user/
```

### 3. Zero-shot 基线评测

```bash
cd /home/user/training
python evaluate_zero_shot.py \
    --model_name Qwen/Qwen2.5-1.5B-Instruct \
    --test_data data/test.jsonl \
    --output_dir ./reports
```

输出: `reports/zero_shot_report.json`

### 4. QLoRA 训练

```bash
python train_qlora.py \
    --model_name Qwen/Qwen2.5-1.5B-Instruct \
    --train_data data/train.jsonl \
    --val_data data/validation.jsonl \
    --output_dir ./checkpoints/lora-adapter \
    --epochs 3 \
    --batch_size 4 \
    --lr 2e-4
```

训练配置:
- 4-bit NF4 量化
- LoRA r=16, alpha=32, dropout=0.05
- 目标层: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj
- 输出: `checkpoints/lora-adapter/final/`

### 5. Adapter 评测

```bash
python evaluate.py \
    --base_model Qwen/Qwen2.5-1.5B-Instruct \
    --adapter_path ./checkpoints/lora-adapter/final \
    --test_data data/test.jsonl \
    --output_dir ./reports
```

输出: `reports/adapter_report.json`

### 6. 对比

比较 `reports/zero_shot_report.json` 和 `reports/adapter_report.json` 中的 Accuracy 与 Macro-F1。

### 7. 下载产物

```bash
# 在本地执行
scp -r user@your-gpu-instance:/home/user/training/reports/ ./
scp -r user@your-gpu-instance:/home/user/training/checkpoints/ ./
```

## 本地测试

```bash
cd training
source ../backend/.venv/bin/activate
python -m pytest tests/test_training_data.py -v
```

只验证数据质量、指标函数和输出 schema，不下载模型、不训练。

## 文件清单

```
training/
├── README.md                   # 本文件
├── data/
│   ├── train.jsonl             # 720 条训练样本
│   ├── validation.jsonl        # 160 条验证样本
│   └── test.jsonl              # 160 条测试样本
├── scripts/
│   ├── generate_data.py        # 数据生成脚本
│   ├── validate_data.py        # 数据校验脚本
│   └── metrics.py              # 指标计算（accuracy/macro-F1/混淆矩阵/错误样例）
├── reports/
│   ├── .gitkeep                         # 真实报告训练后生成于此目录
│   └── 20260627-qlora-result.md         # 2026-06-27 训练结果
├── tests/
│   └── test_training_data.py   # 本地测试
├── train_qlora.py              # QLoRA 训练脚本
├── evaluate_zero_shot.py       # Zero-shot 基线评测
└── evaluate.py                 # Adapter 评测
```
