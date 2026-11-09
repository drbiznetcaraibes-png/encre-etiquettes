-- ENCRE POUR TOUS — Base de données de fiches smartphones (Supabase PostgreSQL).
-- À exécuter dans Supabase > SQL Editor > New query.
-- Les clés API secrètes restent UNIQUEMENT sur le serveur Render.
-- Les prix TTC et mensualités restent propres à chaque étiquette, pas à cette table.

create table if not exists public.phone_specs (
    id bigint generated always as identity primary key,
    -- clé canonique calculée côté serveur, ex: SAMSUNG|GALAXYS26ULTRA5G
    model_key text not null,
    brand text not null,
    model text not null,
    -- Stockage demandé dans la recherche ; confirmation réelle dans result.capacity_confirmed.
    capacity text not null default 'Non précisée',
    manufacturer_reference text,
    specs jsonb not null default '{}'::jsonb,
    sources jsonb not null default '[]'::jsonb,
    search_mode text not null default 'catalogue',
    verification_status text not null default 'à vérifier'
        check (verification_status in ('vérifié', 'à vérifier', 'incomplet')),
    verified_at timestamptz,
    next_check_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (model_key, capacity),
    constraint phone_specs_model_key_nonempty check (length(trim(model_key)) > 1),
    constraint phone_specs_specs_object check (jsonb_typeof(specs) = 'object'),
    constraint phone_specs_sources_array check (jsonb_typeof(sources) = 'array')
);

create index if not exists idx_phone_specs_brand on public.phone_specs (brand);
create index if not exists idx_phone_specs_next_check on public.phone_specs (next_check_at)
    where next_check_at is not null;
create index if not exists idx_phone_specs_model_name on public.phone_specs (lower(model));

-- Accès par défaut BLOQUÉ depuis le navigateur. Seul le serveur backend,
-- avec sa clé secrète Supabase, pourra lire ou modifier ces fiches.
alter table public.phone_specs enable row level security;

-- Pas de politique pour anon / authenticated : aucun accès direct aux fiches
-- par la clé publique. Le backend doit vérifier l'authentification des salariés.

comment on table public.phone_specs is
    'Catalogue centralisé des caractéristiques smartphones vérifiées pour les étiquettes ENCRE POUR TOUS.';

-- V2: une fiche indépendante par recherche exacte (stockage inclus).
alter table public.phone_specs add column if not exists result jsonb not null default '{}'::jsonb;
alter table public.phone_specs add column if not exists last_attempt_at timestamptz;
revoke all on public.phone_specs from anon, authenticated;
grant all on public.phone_specs to service_role;
grant usage, select on sequence public.phone_specs_id_seq to service_role;

-- Une réservation de 0,10 EUR par requête, sans remboursement, même en erreur.
-- 40 requêtes/mois = 4 EUR de réserve; 1 EUR de marge de change/tarifs.
create table if not exists public.ai_usage (
 month date primary key,
 calls integer not null default 0,
 reserved_eur numeric(8,2) not null default 0
);
alter table public.ai_usage enable row level security;
revoke all on public.ai_usage from anon, authenticated;
grant select on public.ai_usage to service_role;
create or replace function public.reserve_ai_call() returns boolean
language plpgsql security definer set search_path = public as $$
declare accepted boolean;
begin
 insert into ai_usage(month) values(date_trunc('month', now())::date) on conflict do nothing;
 update ai_usage set calls=calls+1, reserved_eur=reserved_eur+0.10
 where month=date_trunc('month', now())::date and calls<40 and reserved_eur+0.10<=4.00
 returning true into accepted;
 return coalesce(accepted,false);
end;
$$;
revoke all on function public.reserve_ai_call() from public, anon, authenticated;
grant execute on function public.reserve_ai_call() to service_role;
