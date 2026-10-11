import puppeteer from 'puppeteer-core';
import fs from 'fs';
const [,, mode='stills', ...times]=process.argv;
const b=await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--force-color-profile=srgb','--hide-scrollbars']});
const pg=await b.newPage(); await pg.setViewport({width:1920,height:1080,deviceScaleFactor:1});
await pg.goto('file://'+process.cwd()+'/'+(process.env.SCENE||'scene.html')); await pg.evaluate(()=>window.__init());
if(mode==='stills'){fs.mkdirSync('stills',{recursive:true});
  for(const t of times){await pg.evaluate(t=>__render(t),+t); await pg.screenshot({path:`stills/t${t}.jpg`,type:'jpeg',quality:85});}}
else{fs.mkdirSync(process.env.FR||'frames',{recursive:true}); const N=Math.round((+(process.env.DUR||23))*30);
  const A=+(process.env.FROM||0),B=+(process.env.TO||N);for(let i=A;i<B;i++){await pg.evaluate(t=>__render(t),i/30); await pg.screenshot({path:(process.env.FR||'frames')+`/f${String(i).padStart(4,'0')}.jpg`,type:'jpeg',quality:93}); if(i%60===0)console.log(i);}}
await b.close();
