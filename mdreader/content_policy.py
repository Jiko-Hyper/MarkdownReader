"""Common fragment policy for browser reading and offline exports."""
import html
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

#: Structural tags the reader and the export generator produce themselves
#: (``section``/``nav`` carry the export anchors and the table of contents).
TAGS = set('p br hr h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code strong em del s mark a img table thead tbody tr th td div span section nav article header footer input button details summary'.split())
VOID = {'br','hr','img','input'}

def safe_url(value):
    compact = re.sub(r'[\x00-\x20]+', '', html.unescape(value))
    if compact.startswith('\\'): return False
    try: scheme = urlsplit(compact).scheme.lower()
    except ValueError: return False
    return not scheme or scheme in ('http','https','mailto') or bool(re.match(r'^data:image/(png|jpeg|gif|webp|bmp);base64,',compact,re.I))

class Fragment(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.output=[]
    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:return
        attrs=dict(attrs)
        if tag=='img' and re.match(r'^(https?:)?//',attrs.get('src',''),re.I):
            self.output.append('<span class="resource-warning">远程图片未自动加载：%s</span>' % html.escape(attrs.get('alt','图片')))
            return
        safe=[]
        if tag == 'img':
            width = re.search(r'(?:^|\s)width=(\d+)', attrs.get('title', ''))
            if width:
                safe.append(('width', str(max(16, min(4096, int(width.group(1)))))))
        for key,value in attrs.items():
            value=value or ''
            if key in ('href','src') and safe_url(value): safe.append((key,value))
            elif key in ('id','class','alt','title','start','colspan','rowspan','data-copy','data-md-doc','data-loose-path'):
                safe.append((key,value))
            elif key=='style' and re.fullmatch(r'text-align:\s*(left|right|center);?',value):safe.append((key,value))
        if tag=='input':safe.extend([('type','checkbox'),('disabled','')]); safe += [('checked','')] if 'checked' in attrs else []
        if tag=='button':safe.append(('type','button'))
        if tag=='a':safe.extend([('rel','noopener noreferrer'),('target','_blank')] if re.match(r'https?://',attrs.get('href','')) else [])
        self.output.append('<'+tag+''.join(' %s="%s"'%(k,html.escape(v,quote=True)) for k,v in safe)+'>')
    def handle_endtag(self,tag):
        if tag in TAGS and tag not in VOID:self.output.append('</'+tag+'>')
    def handle_data(self,data):self.output.append(html.escape(data,quote=False))

def sanitize(fragment):
    parser=Fragment();parser.feed(fragment);parser.close();return ''.join(parser.output)
