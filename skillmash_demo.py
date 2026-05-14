from __future__ import annotations

import json

from skillmash.runtime.app_service import SkillMashService


def main() -> None:
    service = SkillMashService()
    task = "甯垜鎼滅储 AI Agent 鏈€鏂拌秼鍔匡紝骞剁敓鎴?PPT"
    result = service.plan(task)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

