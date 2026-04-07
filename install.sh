#!/usr/bin/env bash
set -e

echo "========================================"
echo "  PTSD - 一键安装依赖"
echo "  Product Test Smart Dog"
echo "========================================"
echo

# ── Python 虚拟环境 ──
echo "[1/3] 创建 Python 虚拟环境..."
if [ -f ".venv/bin/python" ]; then
    echo "      已存在，跳过创建"
else
    python3 -m venv .venv || python -m venv .venv
    if [ $? -ne 0 ]; then
        echo "❌ 创建虚拟环境失败！请确认已安装 Python 3.8+"
        echo "   macOS: brew install python3"
        echo "   Linux: sudo apt install python3 python3-venv"
        exit 1
    fi
    echo "      ✅ 虚拟环境创建成功"
fi
echo

# ── Python 依赖 ──
echo "[2/3] 安装 Python 后端依赖..."
.venv/bin/pip install -r backend/requirements.txt --quiet
echo "      ✅ 后端依赖安装完成"
echo

# ── Node.js 前端依赖 ──
echo "[3/3] 安装前端依赖..."
if ! command -v npm &> /dev/null; then
    echo "⚠️  未检测到 npm，请先安装 Node.js 16+"
    echo "   macOS: brew install node"
    echo "   Linux: https://nodejs.org/"
    exit 1
fi
cd frontend
npm install --silent
cd ..
echo "      ✅ 前端依赖安装完成"
echo

echo "========================================"
echo "  ✅ 所有依赖安装完成！"
echo "========================================"
echo
echo "下一步: ./start.sh 启动服务"
