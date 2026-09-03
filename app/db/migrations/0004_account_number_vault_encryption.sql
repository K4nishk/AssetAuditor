-- Migration 0004: pgsodium column encryption for account_number_vault (KCH-66 / AA-29)
--
-- Migration 0001 reserved `account_number_vault.encrypted_account_number` (bytea)
-- and enabled the pgsodium extension but deferred the actual encrypt/decrypt path
-- to this issue ("AA-29 wires the actual column encryption"). This migration wires
-- it: a single project-wide pgsodium key (per-user derived keys are AA-30's
-- stretch scope, not this one — deps: AA-29). Raw key material never leaves
-- pgsodium's own storage; callers only ever hold a key id.
--
-- Real account numbers must go through the two SECURITY DEFINER functions below,
-- never through a raw insert/select against the bytea column: `authenticated`'s
-- table-level insert/update/select grants are revoked, because Postgres has no
-- CHECK constraint that can tell ciphertext bytes from an accidentally-unencrypted
-- plaintext write landing in the same bytea column. `account_id` is folded in as
-- AEAD associated data, so a ciphertext value copied onto a different account_id
-- row fails to decrypt instead of silently decrypting under the wrong account
-- (row-binding, not just column-binding).
--
-- Both functions re-check `auth.uid()` themselves against `public.accounts`/
-- `public.account_number_vault` — SECURITY DEFINER runs as the function owner and
-- therefore bypasses the table's RLS policy, so the tenant check has to be inline
-- SQL here rather than relying on migration 0001's `account_number_vault_tenant_isolation`
-- policy. `set search_path = ''` + fully-qualified references throughout avoid a
-- search-path hijack of a SECURITY DEFINER function (every unqualified name in a
-- SECURITY DEFINER body resolves in the *caller's* search_path unless pinned).

select pgsodium.create_key(name => 'account_number_vault')
where not exists (
    select 1 from pgsodium.valid_key where name = 'account_number_vault'
);

-- Needed for `vault_store_account_number`'s upsert-by-(user_id, account_id); the
-- table otherwise only carries a non-unique index on user_id (migration 0001).
alter table public.account_number_vault
    add constraint account_number_vault_user_account_unique unique (user_id, account_id);

create or replace function public.vault_store_account_number(
    p_account_id uuid,
    p_account_number text
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_user_id uuid;
    v_key_id uuid;
    v_ciphertext bytea;
begin
    select user_id into v_user_id
    from public.accounts
    where id = p_account_id and user_id = auth.uid();

    if v_user_id is null then
        raise exception 'account % not found for the current user', p_account_id;
    end if;

    select id into v_key_id from pgsodium.valid_key where name = 'account_number_vault';
    if v_key_id is null then
        raise exception 'account_number_vault encryption key not found';
    end if;

    v_ciphertext := pgsodium.crypto_aead_det_encrypt(
        convert_to(p_account_number, 'utf8'),
        convert_to(p_account_id::text, 'utf8'),
        v_key_id
    );

    insert into public.account_number_vault (user_id, account_id, encrypted_account_number)
    values (v_user_id, p_account_id, v_ciphertext)
    on conflict (user_id, account_id) do update
        set encrypted_account_number = excluded.encrypted_account_number,
            deactivated_at = null;
end;
$$;

create or replace function public.vault_reveal_account_number(p_account_id uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_key_id uuid;
    v_ciphertext bytea;
begin
    select encrypted_account_number into v_ciphertext
    from public.account_number_vault
    where account_id = p_account_id
      and user_id = auth.uid()
      and deactivated_at is null;

    if v_ciphertext is null then
        return null;
    end if;

    select id into v_key_id from pgsodium.valid_key where name = 'account_number_vault';
    if v_key_id is null then
        raise exception 'account_number_vault encryption key not found';
    end if;

    return convert_from(
        pgsodium.crypto_aead_det_decrypt(
            v_ciphertext,
            convert_to(p_account_id::text, 'utf8'),
            v_key_id
        ),
        'utf8'
    );
end;
$$;

revoke insert, update, select, delete on public.account_number_vault from authenticated;

grant execute on function public.vault_store_account_number(uuid, text) to authenticated;
grant execute on function public.vault_reveal_account_number(uuid) to authenticated;
