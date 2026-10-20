-- Contacts that share a phone number, judged the way this module judges one:
-- on the LAST 10 DIGITS, with every separator stripped. `0917 123 4567`,
-- `+639171234567` and `=09171234567` are one number, and no ORM domain can
-- say so - which is why this is SQL and why `res_partner.phone_last10` /
-- `phone_last10()` in laundry_pos reduce a number the same way.
--
-- Read-only: nothing here writes. It only says which contacts would have to
-- be merged for one-customer-per-number to hold over the whole book.
--
-- ARCHIVED CONTACTS ARE INCLUDED, and flagged. An archived duplicate is
-- invisible to the cashier's search but still holds its number against the
-- duplicate rule, so it is exactly how a second record for somebody already
-- in the book gets made - and leaving it out of this list would hide the
-- cause of the problem the list is about.
--
-- CHILD contacts are left out (`parent_id IS NULL`), because the rule exempts
-- them: a delivery address under a company legitimately carries its parent's
-- number.
--
-- A number with fewer than 10 digits has no key to compare on and cannot be
-- a duplicate of anything, so it is left out too. Those are a separate
-- problem - see `phone_problem()` in laundry_pos.

WITH keyed AS (
    SELECT p.id,
           p.name,
           p.phone,
           p.active,
           p.create_date::date AS created,
           right(regexp_replace(p.phone, '[^0-9]', '', 'g'), 10) AS phone_key
      FROM res_partner p
     WHERE p.parent_id IS NULL
       AND p.phone IS NOT NULL
       AND length(regexp_replace(p.phone, '[^0-9]', '', 'g')) >= 10
),
dups AS (
    SELECT phone_key
      FROM keyed
     GROUP BY phone_key
    HAVING count(*) > 1
)
SELECT count(*) AS numbers_held_twice_or_more,
       (SELECT count(*) FROM keyed k JOIN dups d ON d.phone_key = k.phone_key)
         AS contacts_involved
  FROM dups;

WITH keyed AS (
    SELECT p.id,
           p.name,
           p.phone,
           p.active,
           p.create_date::date AS created,
           right(regexp_replace(p.phone, '[^0-9]', '', 'g'), 10) AS phone_key
      FROM res_partner p
     WHERE p.parent_id IS NULL
       AND p.phone IS NOT NULL
       AND length(regexp_replace(p.phone, '[^0-9]', '', 'g')) >= 10
),
dups AS (
    SELECT phone_key
      FROM keyed
     GROUP BY phone_key
    HAVING count(*) > 1
)
-- Ordered so one number's contacts sit together, the active ones first and
-- the one with the most orders at the top of each: that is the record the
-- others are merged INTO.
SELECT k.phone_key,
       count(*) OVER (PARTITION BY k.phone_key) AS contacts,
       k.id,
       k.name,
       k.phone,
       CASE WHEN k.active THEN ' ' ELSE 'ARCHIVED' END AS state,
       k.created,
       (SELECT count(*) FROM pos_order o WHERE o.partner_id = k.id) AS pos_orders
  FROM keyed k
  JOIN dups d ON d.phone_key = k.phone_key
 ORDER BY contacts DESC, k.phone_key, k.active DESC, pos_orders DESC, k.id;
