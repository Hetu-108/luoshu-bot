#!/usr/bin/env bash
# ============================================
#  洛书 bot —— Linux 依赖安装脚本
# ============================================
# 用法：
#   chmod +x install.sh && ./install.sh
#
# 作用：
#   1. 检查 Python 版本（需要 3.10+）
#   2. 在项目目录下创建 .venv 虚拟环境
#   3. 安装 requirements.txt 中的依赖
#
# 说明：不会改动系统 Python，全部装在项目内的 .venv 里。

set -e

# 解析脚本所在目录（兼容没有 readlink -f 的系统）
_self="$0"
if command -v readlink >/dev/null 2>&1 && readlink -f "$_self" >/dev/null 2>&1; then
    _dir="$(dirname "$(readlink -f "$_self")")"
else
    _dir="$(cd "$(dirname "$_self")" && pwd)"
fi
cd "$_dir"

echo "================================"
echo "    洛书bot 依赖安装 (Linux)"
echo "================================"
echo

# ---- 1. 找一个可用的 python ----
PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        major=${ver%%.*}
        minor=${ver##*.}
        if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then
            PY="$candidate"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "[错误] 未找到 Python 3.10 或更高版本。"
    echo "       请先安装，例如："
    echo "         Ubuntu/Debian:  sudo apt install python3 python3-venv python3-pip"
    echo "         CentOS/RHEL:    sudo yum install python3 python3-pip"
    exit 1
fi

echo "[1/3] 使用 Python：$PY ($("$PY" --version 2>&1))"

# ---- 2. 创建虚拟环境 ----
if [ -d ".venv" ]; then
    echo "[2/3] 已存在 .venv，跳过创建（如需重建请先删除该目录）"
else
    echo "[2/3] 创建虚拟环境 .venv ..."
    "$PY" -m venv .venv
fi

# ---- 3. 安装依赖 ----
echo "[3/3] 安装依赖（首次较慢，请耐心等待）..."
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

echo
echo "================================"
echo "  安装完成！"
echo "  启动： ./start.sh"
echo "================================"
