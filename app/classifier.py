ASSET_RULES = [
    ("DATASET_API", ["dataset","data","api","database","directory","tracker","prices","pricing"]),
    ("INTELLIGENCE_REPORT", ["report","research","benchmark","market","trend","intelligence","analysis"]),
    ("MICRO_SAAS_TOOL", ["tool","generator","converter","automation","dashboard","monitor","software"]),
    ("TEMPLATE_WORKFLOW", ["template","workflow","playbook","prompt","checklist"]),
    ("CONTENT_IP", ["newsletter","guide","course","tutorial","content","video"]),
]
PAIN = ["manual","expensive","alternative","problem","need","wish","slow","difficult","hours","missing"]

def classify(title: str, content: str):
    hay=(title+" "+content[:12000]).lower()
    ranked=[]
    for kind, words in ASSET_RULES:
        hits=sum(1 for w in words if w in hay)
        ranked.append((hits,kind))
    hits,kind=max(ranked)
    pain=sum(1 for w in PAIN if w in hay)
    return kind,hits,pain
