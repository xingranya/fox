"""支持 `python -m brand_os` 调用本地 CLI。"""

from .cli import main


raise SystemExit(main())
