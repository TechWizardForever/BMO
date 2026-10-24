"""What BMO can do on this computer, and why not where he can't (portability plan P1).

Usage: .venv/bin/python print_capabilities.py
"""
import config
from systems.host import capabilities, detect_host, printed_report

if __name__ == "__main__":
    host = detect_host()
    print("BMO capability report")
    print("---------------------")
    print(printed_report(host, capabilities(host, vision_files=config.GUI_VISION_MODEL.exists())))
