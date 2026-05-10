#!/bin/bash
set -e

echo "[rename] 开始执行 lamix 改名脚本"
echo "[rename] 时间: $(date)"

# ── 1. 停 daemon ────────────────────────────────────────────────────────────
echo "[rename] 1/10 停 daemon..."
launchctl unload ~/Library/LaunchAgents/com.lampson.gateway.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.lampson.watchdog.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.lampson.training-reporter.plist 2>/dev/null || true
echo "[rename] daemon 已停"

# ── 2. 备份 ~/.lampson → ~/.lamix ────────────────────────────────────────
echo "[rename] 2/10 改名 ~/.lampson → ~/.lamix"
if [ -d ~/.lamix ]; then
    echo "[rename] ~/.lamix 已存在，先删除..."
    rm -rf ~/.lamix
fi
mv ~/.lampson ~/.lamix
echo "[rename] 完成"

# ── 3. 备份 ~/lampson → ~/lamix ─────────────────────────────────────────
echo "[rename] 3/10 改名 ~/lampson → ~/lamix"
if [ -d ~/lamix ]; then
    echo "[rename] ~/lamix 已存在，先删除..."
    rm -rf ~/lamix
fi
mv ~/lampson ~/lamix
echo "[rename] 完成"

# ── 4. 重建 venv ─────────────────────────────────────────────────────────
echo "[rename] 4/10 重建 venv (~/lamix/.venv)"
cd ~/lamix
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e . --quiet
echo "[rename] venv 重建完成"

# ── 5. 更新 launchd plist ─────────────────────────────────────────────────
echo "[rename] 5/10 更新 launchd plist"

# gateway plist
sed -i '' "s|com\.lampson\.gateway|com.lamix.gateway|g" ~/Library/LaunchAgents/com.lampson.gateway.plist
sed -i '' "s|/Users/songyuhao/lampson|/Users/songyuhao/lamix|g" ~/Library/LaunchAgents/com.lampson.gateway.plist
sed -i '' "s|lampson/logs|lamix/logs|g" ~/Library/LaunchAgents/com.lampson.gateway.plist
sed -i '' "s|lampson/logs|lamix/logs|g" ~/Library/LaunchAgents/com.lampson.gateway.plist
mv ~/Library/LaunchAgents/com.lampson.gateway.plist ~/Library/LaunchAgents/com.lamix.gateway.plist

# watchdog plist
sed -i '' "s|com\.lampson\.watchdog|com.lamix.watchdog|g" ~/Library/LaunchAgents/com.lampson.watchdog.plist
sed -i '' "s|/Users/songyuhao/lampson|/Users/songyuhao/lamix|g" ~/Library/LaunchAgents/com.lampson.watchdog.plist
sed -i '' "s|lampson/logs|lamix/logs|g" ~/Library/LaunchAgents/com.lampson.watchdog.plist
sed -i '' "s|lampson/logs|lamix/logs|g" ~/Library/LaunchAgents/com.lampson.watchdog.plist
mv ~/Library/LaunchAgents/com.lampson.watchdog.plist ~/Library/LaunchAgents/com.lamix.watchdog.plist

# training-reporter plist
sed -i '' "s|com\.lampson\.training-reporter|com.lamix.training-reporter|g" ~/Library/LaunchAgents/com.lampson.training-reporter.plist
sed -i '' "s|lampson/learned_modules|lamix/learned_modules|g" ~/Library/LaunchAgents/com.lampson.training-reporter.plist
sed -i '' "s|lampson/logs|lamix/logs|g" ~/Library/LaunchAgents/com.lampson.training-reporter.plist
sed -i '' "s|lampson/logs|lamix/logs|g" ~/Library/LaunchAgents/com.lampson.training-reporter.plist
mv ~/Library/LaunchAgents/com.lampson.training-reporter.plist ~/Library/LaunchAgents/com.lamix.training-reporter.plist

echo "[rename] plist 更新完成"

# ── 6. 更新 GitHub remote ─────────────────────────────────────────────────
echo "[rename] 6/10 更新 GitHub remote (需手动去 GitHub 网站改名仓库)"
echo "[rename] 当前 remote: $(git remote get-url origin)"

# ── 7. 合并分支 ───────────────────────────────────────────────────────────
echo "[rename] 7/10 合并分支到 master 并推送"
cd ~/lamix
git fetch origin
git checkout master
git merge refactor/rename-to-lamix --no-edit
git push origin master
echo "[rename] 分支已合并并推送"

# ── 8. 重启 daemon ────────────────────────────────────────────────────────
echo "[rename] 8/10 重启 daemon"
launchctl load ~/Library/LaunchAgents/com.lamix.gateway.plist
launchctl load ~/Library/LaunchAgents/com.lamix.watchdog.plist
launchctl load ~/Library/LaunchAgents/com.lamix.training-reporter.plist 2>/dev/null || true
echo "[rename] daemon 已启动"

# ── 9. 验证 ──────────────────────────────────────────────────────────────
echo "[rename] 9/10 验证"
sleep 5
if pgrep -f "lamix.*daemon" > /dev/null; then
    echo "[rename] ✅ daemon 运行正常"
else
    echo "[rename] ⚠️ daemon 可能未正常启动，请检查"
fi

# ── 10. 清理脚本自身 ────────────────────────────────────────────────────
echo "[rename] 10/10 清理脚本"
rm -f ~/lamix/rename_to_lamix.sh

echo "[rename] ✅ 全部完成！"
echo "[rename] 时间: $(date)"
echo "[rename] ⚠️ 手动操作：去 GitHub 将仓库 lampsonSong/lampson 改名为 lamixSong/lamix"
echo "[rename] 然后执行: cd ~/lamix && git remote set-url origin https://github.com/lamixSong/lamix.git"
