-- À exécuter dans le SQL Editor du projet Supabase Biznet Étiquette.
-- Relève le plafond mensuel à 200 tentatives et 20 EUR réservés.
-- Les compteurs existants dans public.ai_usage sont conservés.
begin;

create or replace function public.reserve_ai_call() returns boolean
language plpgsql security definer set search_path = public as $$
declare accepted boolean;
begin
 insert into ai_usage(month) values(date_trunc('month', now())::date) on conflict do nothing;
 update ai_usage set calls=calls+1, reserved_eur=reserved_eur+0.10
 where month=date_trunc('month', now())::date and calls<200 and reserved_eur+0.10<=20.00
 returning true into accepted;
 return coalesce(accepted,false);
end;
$$;
revoke all on function public.reserve_ai_call() from public, anon, authenticated;
grant execute on function public.reserve_ai_call() to service_role;

commit;
