"""Local scanned PDF extraction with editable math and traceable review notes."""
import base64
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re


def select_pages(spec, count):
    if spec == 'all': return list(range(1, count + 1))
    if not isinstance(spec,str) or not re.fullmatch(r'[1-9]\d*(?:-[1-9]\d*)?(?:,[1-9]\d*(?:-[1-9]\d*)?)*',spec):
        raise ValueError('页码格式无效，例如 1-20、25 或 all')
    selected=set()
    for item in spec.split(','):
        ends=item.split('-'); first=int(ends[0]); last=int(ends[-1])
        if first>last or last>count: raise ValueError('页码超出文档范围或起止顺序错误')
        selected.update(range(first,last+1))
    return sorted(selected)


def normalized_math(text):
    return re.sub(r'\s+','',text).removeprefix(r'\[').removesuffix(r'\]')


def convert(conv):
    import pypdfium2 as pdfium
    from PIL import ImageOps
    from mineru.parser import parse
    from mineru.parser.base import ParseResult
    from mineru_llama_cpp import Engine, SamplingParams
    source=pdfium.PdfDocument(str(conv.source))
    engine=None
    source_name=conv.task.options.get('source_name',conv.source.name)
    try:
        pages=select_pages(conv.task.options.get('pages','all'),len(source))
        if len(source)>2000: raise ValueError('当前支持最多 2000 页，请拆分文档')
        with conv.source.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        parts=[f'# {conv.source.stem}\n',
               '> 扫描识别草稿：正文和公式可编辑，公式采用 LaTeX。尚未人工验收，不代表达到 98% 准确率。公式重复识别一致也可能出错；所有正文和公式均需核对。',
               f'原文件：`{source_name}`；SHA-256：`{digest}`；原件共 {len(source)} 页；本次转换 {len(pages)} 页。']
        models=Path(os.environ['MINERU_HOME'])/'models/MinerU2.5-Pro-2605-1.2B-GGUF'
        total_bytes=0
        def asset(data,suffix):
            nonlocal total_bytes
            if len(conv.assets)>=10000 or total_bytes+len(data)>900*1024*1024:
                raise ValueError('图片附件过多，请缩小转换页码范围')
            name=f'scan-{len(conv.assets)+1:05d}{suffix}'
            path=Path(conv.task.path(name));path.write_bytes(data)
            conv.assets.append(str(path));total_bytes+=len(data)
            return conv.assets_name+'/'+name
        for offset in range(0,len(pages),8):
            batch=pages[offset:offset+8]
            conv.task.progress(f'识别 PDF 第 {batch[0]}—{batch[-1]} 页',int(offset/len(pages)*90))
            result=parse(str(conv.source),tier='advanced',page_range=','.join(map(str,batch)),image_analysis=False)
            # Materialize all images within task-owned storage before rendering.
            from mineru.parser.writer import FileBasedDataWriter
            batchdir=Path(conv.task.ensure_dir(f'batch-{offset}'))
            result.save(FileBasedDataWriter(str(batchdir)))
            doc=json.loads((batchdir/'middle_json.json').read_text(encoding='utf-8'))
            mapping={}
            def visit(node):
                if isinstance(node,dict):
                    path=node.get('image_path')
                    if path:
                        local=(batchdir/path).resolve()
                        if not local.is_relative_to(batchdir.resolve()):raise ValueError('识别附件路径无效')
                        if path not in mapping:mapping[path]=asset(local.read_bytes(),local.suffix)
                        node['image_path']=mapping[path]
                    for v in node.values():visit(v)
                elif isinstance(node,list):
                    for v in node:visit(v)
            visit(doc)
            for page in doc['pages']:
                number=page['page_idx']+1
                page_obj=source[number-1]
                bitmap=page_obj.render(scale=2.4)
                original=bitmap.to_pil().convert('RGB');bitmap.close();page_obj.close()
                buf=io.BytesIO();original.save(buf,format='JPEG',quality=88)
                page_url=asset(buf.getvalue(),'.jpg')
                parts.extend([f'\n## PDF 第 {number} 页\n',f'> 原件：{source_name}；PDF 第 {number} 页。[查看原页]({page_url})。本页内容待核对。'])
                for block in page['blocks']:
                    if block['type'] in ('header','footer','page_number'):continue
                    fragment=deepcopy(doc);fragment['pages']=[dict(page,blocks=[block])];fragment['is_full_document']=False
                    content=ParseResult.from_dict(fragment).markdown()
                    bbox=block.get('bbox',[])
                    parts.append(f'<!-- PDF {number}; bbox {bbox} -->\n'+content)
                    if block['type']=='equation':
                        if engine is None:
                            engine=Engine(models/'MinerU2.5-Pro-2605-1.2B-Q8_0.gguf',models/'mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf',n_parallel=1)
                        w,h=original.size;x0,y0,x1,y1=bbox
                        crop=original.crop((max(0,int(x0*w)-8),max(0,int(y0*h)-8),min(w,int(x1*w)+8),min(h,int(y1*h)+8)))
                        crop=ImageOps.autocontrast(ImageOps.grayscale(crop)).convert('RGB')
                        buf=io.BytesIO();crop.save(buf,format='PNG')
                        messages=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()}},{'type':'text','text':'\nFormula Recognition:'}]}]
                        retry=engine.generate(messages,SamplingParams(temperature=0,n_predict=1800))
                        different=normalized_math(retry.content)!=normalized_math(block['content']) or retry.finish_reason!='stop'
                        parts.append(f'> **待核对批注：{"两次识别存在差异，不自动替换" if different else "重复识别一致，仍需人工核对"}。** 原件：{source_name}；PDF 第 {number} 页；区域（左、上、右、下，页面比例）{bbox}。')
                        if block.get('image_path'):parts.append(f'[查看公式原图]({block["image_path"]})')
                        if different:
                            # Fenced candidate is review material, never silently merged into正文.
                            candidate=retry.content.replace('```','')
                            parts.append('备选识别（尚未确认）：\n\n```latex\n'+candidate+'\n```')
                original.close()
        conv.warn('扫描转换完成，准确率尚未验收。请核对批注、上下标和原页；识别一致不等于正确。')
        return '\n\n'.join(parts)+'\n'
    finally:
        if engine is not None:engine.close()
        source.close()
