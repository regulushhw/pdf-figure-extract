"""按原文预先建立的审查契约检查底稿；不证明契约本身正确。
python tools/audit_workbook.py workbook.xlsx audit.json
audit.json: {"rules":[{"kind":"cell","sheet":"数据","cell":"C6",
"expected":-0.21,"source":"PDF p5 Table2"}, ...]}
支持cell、count、sequence、source_records；失败退出1，空规则拒绝通过。
source_records的json相对契约文件，内容为[{sheet,cell,value,method/status},...]。
"""
import json,sys,math,numbers
from pathlib import Path
import openpyxl

def same(a,b,tol=1e-8):
    if isinstance(a,numbers.Real) and isinstance(b,numbers.Real):
        return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(a-b)<=tol
    return type(a) is type(b) and a==b

def audit(workbook,contract):
    contract=Path(contract);config=json.loads(contract.read_text(encoding='utf-8-sig'))
    rules=config.get('rules',[])
    if not rules:raise ValueError('无审查规则，不能通过')
    wb=openpyxl.load_workbook(workbook,data_only=True);failures=[];checked=0
    for index,rule in enumerate(rules):
        try:
            if not rule.get('source'):raise ValueError('缺少原文/依据定位')
            kind=rule['kind']
            if kind=='source_records':
                rows=json.loads((contract.parent/rule['json']).read_text(encoding='utf-8-sig'))
                if not rows:raise ValueError('源记录为空')
                for r in rows:
                    got=wb[r['sheet']][r['cell']].value;want=r['value']
                    if not same(got,want):raise ValueError(f"源值与底稿不符：{r['sheet']}!{r['cell']}")
                    if want==0 and r.get('method') not in ('original_table','explicit_zero_height','baseline_visually_near_zero'):
                        raise ValueError(f"0缺少明确依据：{r['cell']}")
                    if r.get('status')=='missing' and got is not None:raise ValueError('缺值被填入数值')
                    checked+=1
            elif kind=='cell':
                got=wb[rule['sheet']][rule['cell']].value
                if not same(got,rule['expected']):raise ValueError(f"身份/单位/数值不符：{rule['cell']} got={got!r}")
                checked+=1
            elif kind in ('count','sequence'):
                cells=[c for row in wb[rule['sheet']][rule['range']] for c in row]
                if kind=='count':
                    n=sum(isinstance(c.value,numbers.Real) and not isinstance(c.value,bool) for c in cells)
                    if n!=rule['expected']:raise ValueError(f"数值覆盖{n}，预期{rule['expected']}")
                elif len(cells)!=len(rule['expected']) or not all(same(c.value,v) for c,v in zip(cells,rule['expected'])):
                    raise ValueError('坐标/日期标签不符')
                checked+=1
            else:raise ValueError(f'未知检查类型：{kind}')
        except Exception as exc:failures.append({'rule':index,'error':str(exc)})
    return {'checked':checked,'failures':failures,'scope':'declared_contract_only; original_source_review_still_required'}

if __name__=='__main__':
    try:result=audit(sys.argv[1],sys.argv[2])
    except Exception as exc:result={'failures':[str(exc)]}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    sys.exit(1 if result['failures'] else 0)
