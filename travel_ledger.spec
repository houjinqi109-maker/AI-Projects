# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
icon = str(root / "assets" / "travel-ledger.ico")
analysis = Analysis([str(root / "travel_ledger.py")], pathex=[str(root)],
                    binaries=[], datas=[(icon, "assets")], hiddenimports=[])
archive = PYZ(analysis.pure)
exe = EXE(archive, analysis.scripts, analysis.binaries, analysis.datas, [],
          name="旅途账本", icon=icon, console=False, upx=False)
