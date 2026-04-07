#!/usr/bin/env bash
set -e

echo "========================================"
echo "  PTSD - Product Test Smart Dog"
echo "  AI 驱动的产品智能评测系统"
echo "========================================"
echo

# ── 检查依赖 ──
if [ ! -f ".venv/bin/python" ]; then
    echo "❌ 未找到 Python 虚拟环境，请先运行 ./install.sh"
    exit 1
fi

# ── 清理旧进程 ──
echo "[1/3] 清理旧进程..."
lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1
echo "      ✅ 完成"
echo

# ── 启动后端 ──
echo "[2/3] 启动后端服务..."
.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
sleep 3
echo "      ✅ 后端已启动: http://localhost:8000 (PID: $BACKEND_PID)"
echo

# ── 启动前端 ──
echo "[3/3] 启动前端服务..."
if command -v npm &> /dev/null; then
    cd frontend && npm run dev &
    FRONTEND_PID=$!
    cd ..
    echo "      ✅ 前端已启动: http://localhost:3000 (PID: $FRONTEND_PID)"
else
    echo "      ⚠️  未检测到 npm，前端需手动启动: cd frontend && npm run dev"
fi
echo

echo "========================================"
echo "  🚀 启动完成！"
echo "========================================"
echo
echo "  前端页面:  http://localhost:3000"
echo "  后端 API:  http://localhost:8000"
echo "  API 文档:  http://localhost:8000/docs"
echo
echo "  按 Ctrl+C 停止所有服务"
echo

# 等待前台进程
wait
