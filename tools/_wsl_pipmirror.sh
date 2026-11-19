set -u
VENV="$HOME/.orch-venv"
for M in https://pypi.tuna.tsinghua.edu.cn/simple https://mirrors.aliyun.com/pypi/simple; do
  echo "== try $M =="
  if "$VENV/bin/python" -m pip install -q --disable-pip-version-check \
        --default-timeout 90 --retries 2 -i "$M" "mcp>=1.2" 2>&1 | tail -n 2; then
    if "$VENV/bin/python" -c "import mcp" 2>/dev/null; then echo "  OK via $M"; exit 0; fi
  fi
done
echo "  x 两个镜像都没装上"
exit 4