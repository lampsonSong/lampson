"""Lamix 卸载程序入口。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.install_windows import uninstall
if __name__ == "__main__":
    uninstall()
    input("\n按回车键退出...")
