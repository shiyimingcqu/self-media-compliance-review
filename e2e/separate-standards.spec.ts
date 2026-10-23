import { test, expect } from '@playwright/test';
const base='http://127.0.0.1:8001/api/v1';
const headers=()=>({'Idempotency-Key':crypto.randomUUID()});

test('Brief参考资料页初次加载及刷新保持固定Hook顺序',async({page,request})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const project=(await(await request.post(`${base}/projects`,{headers:headers(),data:{name:'Brief加载回归',brandName:'品牌',platforms:['douyin']}})).json()).data;
  const file=(await(await request.post(`${base}/files`,{headers:headers(),multipart:{purpose:'brief',file:{name:'brief.txt',mimeType:'text/plain',buffer:Buffer.from('请在脚本中安排品牌介绍和产品展示。')}}})).json()).data;
  const brief=(await(await request.post(`${base}/projects/${project.id}/briefs`,{headers:headers(),data:{fileId:file.id,title:'品牌参考资料',platforms:['douyin']}})).json()).data;
  await page.goto(`/projects/${project.id}/briefs/${brief.briefId}`);
  await expect(page.getByRole('button',{name:'创建首个规则草稿'})).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button',{name:'创建首个规则草稿'})).toBeVisible();
  expect(errors).toEqual([]);
});
