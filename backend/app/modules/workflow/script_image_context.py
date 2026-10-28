"""Attach provable document labels to images, without deciding what will be filmed."""
import copy
import re

REFERENCE_LABELS = {'参考图','参考图片','参考画面','风格参考','特效参考','动作参考','参考截图','视频参考截图','效果参考','参考视频','镜头参考'}
PLANNED_LABELS = {'拟拍画面','拟拍摄画面','本次拍摄画面','实际拍摄画面','成片截图','产品实拍图'}


def position(location):
    table = re.fullmatch(r'table:(\d+):r(\d+)c(\d+)', location)
    if table:
        return ('table:'+table[1], int(table[2]), int(table[3]))
    sheet = re.fullmatch(r'(.+)!([A-Z]+)([1-9]\d*)', location)
    if sheet:
        return ('sheet:'+sheet[1], int(sheet[3]), sheet[2])
    return None


def annotate_image_context(manifest, blocks):
    result = copy.deepcopy(manifest)
    indexed = [(block, position(block.get('location') or '')) for block in blocks]
    for image in result.get('images', []):
        contexts = []
        for location in image.get('locations', []):
            context = {'location': location, 'purpose': 'unknown'}
            anchor = position(location)
            if anchor:
                owner, row, col = anchor
                headers = []
                for block, coord in indexed:
                    if not coord or coord[0]!=owner or coord[2]!=col or coord[1]>row:
                        continue
                    label = re.sub(r'\s+', '', block.get('text','')).strip('：:')
                    if label in REFERENCE_LABELS | PLANNED_LABELS:
                        headers.append((coord[1], block, label))
                if headers:
                    _, header, label = max(headers, key=lambda item:item[0])
                    context.update(purpose='reference' if label in REFERENCE_LABELS else 'planned',
                                   label=label, labelLocation=header['location'],
                                   labelBlockId=header.get('paragraphId'))
                context['rowBlockIds'] = [block['paragraphId'] for block,coord in indexed
                                          if coord and coord[0]==owner and coord[1]==row
                                          and coord[2]!=col and block.get('paragraphId')]
            contexts.append(context)
        purposes = {context['purpose'] for context in contexts}
        image['purpose'] = next(iter(purposes)) if len(purposes)==1 else 'mixed' if purposes else 'unknown'
        image['contexts'] = contexts
    return result
