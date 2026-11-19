set -u
echo "== apt-get update =="
apt-get update -qq 2>&1 | tail -n 2
echo "== install python3-pip =="
apt-get install -y -qq python3-pip 2>&1 | tail -n 3
python3 -m pip -V 2>&1 | tail -n 1