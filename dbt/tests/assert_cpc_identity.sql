-- CPC = CPM / (1000 x CTR) is an arithmetic identity, and the CPC
-- decomposition (splitting a cost rise into its CPM/auction and CTR/creative
-- components) is the engine's headline commercial analysis. Currently 0 of
-- 4,003 rows violate it; a persisted test costs nothing and protects it
-- permanently.
select campaign_key, ad_date, cpc, cpm, ctr
from {{ ref('fct_ad_spend_daily') }}
where clicks > 0 and impressions > 0
  and abs(cpc - cpm / (1000.0 * ctr)) > 0.01
