"""Local, bounded PDF page rasterization for source previews; never an AI call."""
import io
import threading

from app.core.errors import ApiError

PDF_LOCK=threading.RLock()


def render_pdf_page(path,page_no):
    import pypdfium2 as pdfium
    try:
        with PDF_LOCK,pdfium.PdfDocument(str(path)) as document:
            if page_no<1 or page_no>len(document):raise ApiError(404,'RESOURCE_NOT_FOUND','预览页码不存在')
            page=document[page_no-1]
            try:
                width,height=page.get_size()
                bitmap=page.render(scale=min(1.7,2400/max(width,height)))
                try:
                    output=io.BytesIO();bitmap.to_pil().save(output,format='PNG')
                finally:bitmap.close()
            finally:page.close()
    except ApiError:raise
    except Exception:raise ApiError(422,'DOCUMENT_PREVIEW_INVALID','此页原布局读取失败，请重试或下载原文件核对。') from None
    return output.getvalue()
