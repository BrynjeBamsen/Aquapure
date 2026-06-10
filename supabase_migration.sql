-- AquaPure: kobling mellem Supabase-brugere og Stripe-kunder
-- Kør i Supabase SQL editor (eller som migration)

create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  stripe_customer_id text unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- Brugere må læse deres egen profil (skrivning sker kun server-side med service key)
create policy "read own profile"
  on public.profiles for select
  using (auth.uid() = user_id);

create index if not exists profiles_stripe_customer_idx on public.profiles (stripe_customer_id);
