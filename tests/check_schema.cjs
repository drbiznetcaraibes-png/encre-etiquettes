// Run with PGLITE_MODULE pointing to an installed @electric-sql/pglite package.
const {PGlite}=require(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const db=new PGlite();
 await db.exec('create role anon; create role authenticated; create role service_role;');
 const sql=fs.readFileSync('supabase_schema.sql','utf8');
 await db.exec(sql);
 await db.exec(sql); // Migration is repeatable.
 const results=await Promise.all(Array.from({length:60},()=>db.query('select reserve_ai_call() as accepted')));
 assert.equal(results.filter(r=>r.rows[0].accepted).length,40);
 const usage=await db.query('select calls, reserved_eur from ai_usage');
 assert.equal(usage.rows[0].calls,40);
 assert.equal(Number(usage.rows[0].reserved_eur),4);
 await db.exec('set role anon');
 await assert.rejects(()=>db.query('select * from phone_specs'));
 await assert.rejects(()=>db.query('select reserve_ai_call()'));
 await db.exec('reset role; set role service_role');
 assert.equal((await db.query('select reserve_ai_call() as accepted')).rows[0].accepted,false);
 await assert.rejects(()=>db.query('update ai_usage set calls=0'));
 await db.close();
 console.log('SQL OK: repeatable schema, 40/60 accepted, EUR 4 ceiling, role permissions');
})().catch(e=>{console.error(e);process.exit(1)});
