"""Exercise the shipped scan command through the desktop host/worker boundary."""
import argparse,json,re,sys,tempfile,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from mdreader import core
from mdreader.conversion import convert_file

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--pages',default='16,329')
    args=p.parse_args()
    base=ROOT/'artifacts/scan-ocr-qualification';base.mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix='run-',dir=base))
    ws=core.Workspace(str(folder/'workspace'))
    try:
        ws.plugins.install(str(ROOT/'plugins/packages/mdreader.document-convert-1.4.0.zip'))
        ws.plugins.enable('mdreader.document-convert')
        command=next(c['command'] for c in ws.plugins.commands() if c.get('format')=='scan-md')
        result=convert_file(ws.plugins,command,args.source,str(folder/'result.md'),pages=args.pages)
        text=Path(result['path']).read_text(encoding='utf-8')
        checks={'editable_math':'$$' in text,'review_notes':'待核对批注' in text,
                'formula_crops':'查看公式原图' in text, 'diagram':'![' in text,
                'no_accuracy_claim':'不代表达到 98%' in text,'source_identity':Path(args.source).name in text,
                'linked_assets':all((folder/s).exists() for s in re.findall(r'\]\(([^)]+)\)',text))}
        cancelled=threading.Event();cancelled.set()
        try:
            convert_file(ws.plugins,command,args.source,str(folder/'cancelled.md'),pages='16',cancel=cancelled)
        except Exception:
            checks['cancel_does_not_publish']=not (folder/'cancelled.md').exists()
        else:
            checks['cancel_does_not_publish']=False
        try:
            convert_file(ws.plugins,command,args.source,str(folder/'invalid.md'),pages='999999')
        except Exception:
            checks['bad_range_does_not_publish']=not (folder/'invalid.md').exists()
        else:
            checks['bad_range_does_not_publish']=False
        report={'checks':checks,'passed':all(checks.values()),'result':result}
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False));return 0 if report['passed'] else 1
    finally:ws.plugins.shutdown()
if __name__=='__main__':raise SystemExit(main())
