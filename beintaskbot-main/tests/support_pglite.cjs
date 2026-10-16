// An isolated PostgreSQL/WASM process for the Python integration tests. No ports or external databases.
const readline = require('node:readline');
const { PGlite } = require('../.support-test-deps/node_modules/@electric-sql/pglite');
(async()=>{
  const db = new PGlite(); await db.waitReady;
  const input = readline.createInterface({input:process.stdin});
  for await (const line of input) {
    try {
      const {sql,params=[]} = JSON.parse(line);
      const values=params.map(v=>v&&v.__bytes?Buffer.from(v.__bytes,'hex'):v);
      const result=await db.query(sql,values);
      process.stdout.write(JSON.stringify({rows:result.rows},(_k,v)=>v instanceof Uint8Array?{__bytes:Buffer.from(v).toString('hex')}:typeof v==='bigint'?Number(v):v)+'\n');
    } catch(error) { process.stdout.write(JSON.stringify({error:error.message,code:error.code})+'\n'); }
  }
  await db.close();
})();
