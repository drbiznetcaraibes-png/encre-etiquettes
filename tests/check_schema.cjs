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
 const functionDefinition=()=>db.query("select pg_get_functiondef('public.reserve_ai_call()'::regprocedure) as definition");
 const schemaDefinition=await functionDefinition();
 await db.exec("insert into ai_usage(month, calls, reserved_eur) values (date_trunc('month', now())::date, 40, 4.00)");
 const quotaMigration=fs.readFileSync('migrations/2026-11-23-plafond-ia-20-euros.sql','utf8');
 await db.exec(quotaMigration);
 await db.exec(quotaMigration);
 assert.deepEqual((await functionDefinition()).rows,schemaDefinition.rows);
 assert.equal((await db.query('select calls from ai_usage')).rows[0].calls,40);
 const results=await Promise.all(Array.from({length:180},()=>db.query('select reserve_ai_call() as accepted')));
 assert.equal(results.filter(r=>r.rows[0].accepted).length,160);
 const usage=await db.query('select calls, reserved_eur from ai_usage');
 assert.equal(usage.rows[0].calls,200);
 assert.equal(Number(usage.rows[0].reserved_eur),20);
 await db.exec('set role anon');
 await assert.rejects(()=>db.query('select * from phone_specs'));
 await assert.rejects(()=>db.query('select reserve_ai_call()'));
 await db.exec('reset role; set role service_role');
 assert.equal((await db.query('select reserve_ai_call() as accepted')).rows[0].accepted,false);
 await assert.rejects(()=>db.query('update ai_usage set calls=0'));
 await db.close();
 console.log('SQL OK: repeatable migration, 40 existing attempts preserved, 200 attempts and EUR 20 ceiling, role permissions');
})().catch(e=>{console.error(e);process.exit(1)});
