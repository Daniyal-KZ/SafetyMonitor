"""One class contract shared by preparation, training and inference."""
CLASS_NAMES = ("PERSON", "HELMET", "VEST", "GLOVES", "PPE GLASSES")
RULE_CLASSES = {"helmet": "HELMET", "vest": "VEST", "gloves": "GLOVES", "glasses": "PPE GLASSES"}
RULE_LABELS = {"helmet": "Каска", "vest": "Сигнальный жилет", "gloves": "Перчатки", "glasses": "Защитные очки"}


def canonical_name(name: str) -> str | None:
    normalized = name.lower().strip().replace("_", " ").replace("-", " ")
    return {
        "person": "PERSON", "helmet": "HELMET", "hardhat": "HELMET",
        "vest": "VEST", "safety vest": "VEST", "gloves": "GLOVES",
        "goggles": "PPE GLASSES", "glasses": "PPE GLASSES", "ppe glasses": "PPE GLASSES",
        "no helmet": "NO HELMET", "no hardhat": "NO HELMET",
        "no goggle": "NO PPE GLASSES", "no goggles": "NO PPE GLASSES",
        "no glasses": "NO PPE GLASSES", "no ppe glasses": "NO PPE GLASSES",
        "no gloves": "NO GLOVES",
        "no vest": "NO VEST", "no safety vest": "NO VEST",
    }.get(normalized)
