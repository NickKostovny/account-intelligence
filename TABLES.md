# All tables

Header rows only, read from the live tables on 2026-10-06. No data rows are in any repo.

## `data/accounts.csv`

74 rows. Columns:

`account_id`, `company`, `in_universe`, `active`, `exclude`, `hq`, `est_revenue`, `primary_modalities`, `icp_fit_company`, `pipeline_status`, `deal_owner`, `key_invert_contact`, `notes`, `modality_footprint`, `lineage_summary`, `digital_data_it_location`, `digital_data_it_confidence`, `it_centralization_hypothesis`, `it_centralization_confidence`, `decision_power_hypothesis`, `decision_power_confidence`, `company_news_latest`, `last_refreshed`

## `data/ats_census.csv`

49 rows. Columns:

`account_id`, `company`, `careers_url`, `ats`, `ats_host`, `bucket`, `api_shape`, `jsonld_jobposting`, `http`, `notes`, `probed_at`

## `data/careers.csv`

49 rows. Columns:

`account_id`, `company`, `careers_host`, `pattern_kind`, `req_path_glob`, `url_sample`, `req_urls_found`, `city_from_url`, `probe_status`, `candidates_tried`, `probed_at`, `notes`, `workday_host`, `workday_site`

## `data/careers_probe.csv`

40 rows. Columns:

`account_id`, `company`, `careers_host`, `robots`, `robots_allows_jobs`, `sitemap_url`, `sitemap_job_urls`, `jsonld_sampled`, `jsonld_hits`, `date_posted_sample`, `location_structured`, `req_identifier`, `body_chars_median`, `verdict`, `notes`, `probed_at`

## `data/clay_targets.csv`

189 rows. Columns:

`target_id`, `account_id`, `company`, `domain`, `priority`, `pipeline_status`, `site_ids`, `site`, `name`, `title_hint`, `source_text`

## `data/jobs_runs.csv`

14 rows. Columns:

`run_id`, `kind`, `started_at`, `finished_at`, `accounts`, `urls_seen`, `new_urls`, `extracted`, `quarantined`, `verify_pass_rate`, `notes`

## `data/org_edges.csv`

98 rows. Columns:

`edge_id`, `account_id`, `edge_type`, `src_unit_id`, `dst_unit_id`, `dst_site_id`, `src_label`, `dst_label`, `n_postings`, `n_postings_tier3plus`, `n_quotes`, `n_independent_postings`, `first_evidence_date`, `last_evidence_date`, `evidence_date_kind`, `evidence_posting_ids`, `evidence_quote_ids`, `confidence`, `confidence_basis`, `status`, `human_state`, `human_note`, `generated_at`

## `data/org_site_coverage.csv`

604 rows. Columns:

`account_id`, `site_id`, `site_name`, `city`, `icp_fit`, `n_indexed`, `n_admitted`, `n_bodies`, `n_extracts`, `n_units`, `first_seen`, `last_seen`, `coverage_state`, `scanned_at`

## `data/org_units.csv`

89 rows. Columns:

`unit_id`, `account_id`, `unit_slug`, `unit_name_display`, `unit_name_generated`, `acronym`, `acronym_source`, `aliases`, `unit_kind`, `site_id`, `site_resolution`, `site_candidates`, `parent_unit_id`, `parent_label`, `parent_conf`, `n_postings`, `n_postings_tier3plus`, `n_quotes`, `first_evidence_date`, `last_evidence_date`, `evidence_date_kind`, `job_families`, `seniority_max`, `tech_stack`, `vocabulary`, `hiring_managers`, `evidence_posting_ids`, `evidence_quote_ids`, `status`, `confidence`, `confidence_basis`, `gap_flags`, `human_state`, `human_note`, `generated_at`

## `data/people.csv`

1854 rows. Columns:

`person_id`, `account_id`, `site_id`, `name`, `title`, `dept`, `seniority`, `linkedin_url`, `email`, `persona`, `why_champion`, `surfaced_via`, `source_url`, `last_activity`

## `data/posting_extracts.csv`

32 rows. Columns:

`posting_id`, `account_id`, `site_id_at_extract`, `team_name`, `team_slug`, `team_acronym`, `team_name_conf`, `parent_org`, `parent_slug`, `parent_conf`, `supports_teams`, `supports_slugs`, `reports_to_title`, `hiring_manager`, `hiring_manager_conf`, `site_stated`, `posted_date_verbatim`, `tech_stack`, `tech_stack_raw`, `vocabulary`, `autonomy_flag`, `acquisition_flag`, `n_quotes`, `n_quotes_verified`, `n_claims_dropped`, `body_chars`, `extraction_tier`, `extract_run_id`, `extract_status`, `extract_notes`, `captured_at`

## `data/posting_quotes.csv`

403 rows. Columns:

`quote_id`, `posting_id`, `account_id`, `claim_type`, `subject`, `subject_slug`, `quote_text`, `verbatim_verified`, `match_method`, `display_date`, `date_kind`, `extraction_tier`, `extract_run_id`, `captured_at`

## `data/posting_rollup.csv`

640 rows. Columns:

`rollup_id`, `scope`, `account_id`, `site_id`, `period`, `dim_kind`, `dim_key`, `n`, `denom`, `share`, `account_alltime_share`, `self_index`, `peer_median_share`, `peer_n_accounts`, `peer_index`, `bias_flag`, `bias_note`, `generated_at`

## `data/postings.csv`

32038 rows. Columns:

`posting_id`, `account_id`, `mirror_account_id`, `mirror_site_id`, `mirror_basis`, `site_id`, `site_resolution`, `site_candidates`, `city_slug`, `city_raw`, `also_at`, `icp_fit`, `role_title`, `role_title_source`, `role_slug`, `req_id`, `url_form`, `careers_host`, `job_family`, `job_family_rule`, `seniority_rank`, `seniority_rule`, `excluded`, `tier2_admit`, `tier2_score`, `url`, `first_seen`, `first_seen_kind`, `last_seen`, `last_seen_kind`, `seen_count`, `period_first`, `period_last`, `posted_date`, `posted_date_source`, `extraction_tier`, `body_chars`, `extract_status`, `captured_at`, `updated_at`, `last_run_id`

## `data/quarantine_org.csv`

0 rows. Columns:

`posting_id`, `account_id`, `claim_type`, `subject`, `quote_text`, `reason`, `body_chars`, `checked_at`

## `data/signals.csv`

559 rows. Columns:

`signal_id`, `account_id`, `site_id`, `signal_type`, `title`, `date`, `named_people`, `topic`, `pain_hypothesis`, `tools_named`, `location`, `confidence`, `url`, `source`, `captured_at`

## `data/site_heads.csv`

189 rows. Columns:

`site_head_id`, `account_id`, `site_ids`, `name`, `title_hint`, `found`, `match`, `clay_name`, `clay_title`, `clay_company`, `linkedin_url`, `location`, `email`, `email_status`, `entity_id`, `task_id`, `checked_at`, `source`

## `data/sites.csv`

604 rows. Columns:

`site_id`, `account_id`, `region`, `site`, `city`, `metro`, `legal_entity`, `acquired_lineage`, `functions`, `modality`, `icp_fit`, `icp_fit_reason`, `verdict_auto`, `verdict_manual`, `ae`, `named_site_head`, `evidence_titles`, `source`, `decision_power_hypothesis`, `decision_power_confidence`, `site_news_latest`, `last_refreshed`

## `data/unmatched_cities.csv`

1590 rows. Columns:

`account_id`, `city_raw`, `key`, `n`, `n_admitted`, `first_seen`, `last_seen`, `verdict`

## `data/jev/account_hiring.csv`

29 rows. Columns:

`account_id`, `sites_with_postings`, `mirror_postings`, `postings`, `n_lab_systems`, `n_automation_mes`, `n_data_digital`, `n_msat_mfg_sci`, `n_cell_gene`, `n_process_dev`, `n_analytical`, `n_other`, `n_unsure`, `n_excluded`, `n_not_labeled`, `n_digital_data_it`, `n_bioprocess_core`, `n_last_365d`, `n_digital_data_it_365d`, `n_bioprocess_core_365d`, `newest_last_seen`, `window_from`, `family_from_regex`, `family_from_jev`, `model`

## `data/jev/account_profile.csv`

64 rows. Columns:

`account_id`, `company`, `n_sites`, `modality_footprint_jev`, `modality_unsure_sites`, `function_counts`, `primary_modalities_ssot`, `modality_footprint_ssot`, `fn_research_sites`, `fn_process_development_sites`, `fn_msat_sites`, `fn_cmc_sites`, `fn_manufacturing_sites`, `fn_drug_substance_sites`, `fn_fill_finish_sites`, `fn_qc_sites`, `fn_analytical_development_sites`, `mod_mab_sites`, `mod_adc_sites`, `mod_other_protein_sites`, `mod_vaccine_sites`, `mod_cell_therapy_sites`, `mod_gene_therapy_sites`, `mod_mrna_lnp_sites`, `mod_rnai_oligo_sites`, `mod_peptide_sites`, `mod_small_molecule_sites`, `mod_plasma_sites`, `model`

## `data/jev/check206_final.csv`

167 rows. Columns:

`site_id`, `account_id`, `site`, `city`, `claude_map`, `policy_map`, `jev_verdict`, `final_verdict`, `how`, `reason`, `source_url`, `source_quote`, `source_kind`, `source_date`, `map_right`, `jev_right`

## `data/jev/check206_verification.csv`

167 rows. Columns:

`site_id`, `account_id`, `site`, `city`, `summary_verdict`, `summary_tag`, `claude_verdict`, `found`, `sources`, `sources_fetched`, `excerpts_verified`, `agent_note`, `best_url`, `best_kind`, `best_date`, `about_site_p`, `evidence_verdict`, `evidence_tag`, `evidence_unsure`, `supports`, `conflicts`, `outcome`

## `data/jev/digital_it_location.csv`

74 rows. Columns:

`account_id`, `company`, `postings`, `n_digital_layer`, `n_vetoed`, `n_digital`, `n_digital_365d`, `n_located`, `n_located_from_title`, `n_remote`, `n_no_location`, `n_locations`, `n_sites`, `n_cities`, `top_location`, `top_kind`, `top_id`, `top_n`, `top_share`, `top3_share`, `location_label`, `location_shares`, `n_global_titled`, `n_global_unsure`, `global_titled_share`, `global_scope_p_mean`, `global_titled_display`, `top_global_titled_share`, `n_site_titled`, `site_titled_share`, `site_scope_p_mean`, `mirror_digital`, `newest_last_seen`, `caveat`, `model`

## `data/jev/digital_title_scope.csv`

3194 rows. Columns:

`title_key`, `role_title`, `n_postings`, `n_accounts`, `digital_source`, `regex_family`, `global_scope_p`, `global_scope_gated`, `site_scope_p`, `site_scope_gated`, `digital_data_it_p`, `digital_data_it_gated`, `veto`, `kw_global`, `kw_site`, `model`

## `data/jev/mvp_final.csv`

60 rows. Columns:

`site_id`, `account_id`, `site`, `city`, `claude_map`, `policy_map`, `jev_verdict`, `final_verdict`, `how`, `reason`, `source_url`, `source_quote`, `source_kind`, `source_date`, `map_right`, `jev_right`

## `data/jev/mvp_verification.csv`

60 rows. Columns:

`site_id`, `account_id`, `site`, `city`, `summary_verdict`, `summary_tag`, `claude_verdict`, `found`, `sources`, `sources_fetched`, `excerpts_verified`, `agent_note`, `best_url`, `best_kind`, `best_date`, `about_site_p`, `evidence_verdict`, `evidence_tag`, `evidence_unsure`, `supports`, `conflicts`, `outcome`

## `data/jev/posting_labels.csv`

13034 rows. Columns:

`title_key`, `role_title`, `company_sent`, `set`, `n_postings`, `n_accounts`, `regex_family`, `job_family_jev`, `job_family_conf`, `job_family_p`, `job_family_gated`, `job_family_probs`, `digital_data_it_p`, `digital_data_it_gated`, `bioprocess_core_p`, `bioprocess_core_gated`, `seniority_jev`, `seniority_conf`, `seniority_gated`, `model`

## `data/jev/posting_labels_sample.csv`

400 rows. Columns:

`title_key`, `role_title`, `company_sent`, `set`, `n_postings`, `n_accounts`, `regex_family`, `job_family_jev`, `job_family_conf`, `job_family_p`, `job_family_gated`, `job_family_probs`, `digital_data_it_p`, `digital_data_it_gated`, `bioprocess_core_p`, `bioprocess_core_gated`, `seniority_jev`, `seniority_conf`, `seniority_gated`, `model`

## `data/jev/publication_relevance.csv`

512 rows. Columns:

`signal_id`, `account_id`, `site_id`, `date`, `relevance_score`, `relevance_conf`, `relevance_probs`, `relevance_band`, `process_data_noul`, `process_data`, `keep`, `rank_in_account`, `claude_confidence`, `scrubbed_name`, `model`

## `data/jev/publication_relevance_by_account.csv`

49 rows. Columns:

`account_id`, `n`, `kept`, `band_not_product`, `band_adjacent`, `band_cmc`, `band_process`, `band_unsure`, `process_data_yes`, `claude_low`, `claude_low_below_bar`

## `data/jev/site_fit.csv`

604 rows. Columns:

`site_id`, `account_id`, `region`, `jev_verdict`, `reason_tag`, `info_tag`, `unsure_on`, `icp_fit`, `icp_fit_display`, `explanation`, `claude_verdict`, `claude_icp_fit`, `policy_verdict`, `changed_by_policy`, `policy_reason`, `agree_verdict`, `agree_icp_fit`, `short_text`, `leaked_marker`, `kw_pd`, `kw_modality`, `drug_substance_p`, `drug_substance`, `pd_msat_p`, `pd_msat`, `drug_product_p`, `drug_product`, `manufacturing_p`, `manufacturing`, `support_p`, `support`, `research_p`, `research`, `cdmo_p`, `cdmo`, `status_top`, `status_conf`, `status`, `modality_top`, `modality_conf`, `modality`, `business_top`, `business_conf`, `business`, `status_p_closed`, `status_p_planned`, `model`

## `data/jev/site_hiring.csv`

78 rows. Columns:

`site_id`, `account_id`, `site`, `postings`, `n_lab_systems`, `n_automation_mes`, `n_data_digital`, `n_msat_mfg_sci`, `n_cell_gene`, `n_process_dev`, `n_analytical`, `n_other`, `n_unsure`, `n_excluded`, `n_not_labeled`, `n_digital_data_it`, `n_bioprocess_core`, `n_last_365d`, `n_digital_data_it_365d`, `n_bioprocess_core_365d`, `newest_last_seen`, `window_from`, `family_from_regex`, `family_from_jev`, `model`

## `data/jev/site_label_sample.csv`

60 rows. Columns:

`site_id`, `account`, `site`, `city`, `functions`, `modality`, `nick_verdict`, `nick_note`

## `data/jev/site_profile.csv`

604 rows. Columns:

`site_id`, `account_id`, `region`, `site`, `city`, `cgt_rule`, `function_chips`, `function_unsure`, `modality_chips`, `modality_unsure`, `fn_research_p`, `fn_research`, `fn_process_development_p`, `fn_process_development`, `fn_msat_p`, `fn_msat`, `fn_cmc_p`, `fn_cmc`, `fn_manufacturing_p`, `fn_manufacturing`, `fn_drug_substance_p`, `fn_drug_substance`, `fn_fill_finish_p`, `fn_fill_finish`, `fn_qc_p`, `fn_qc`, `fn_analytical_development_p`, `fn_analytical_development`, `mod_mab_p`, `mod_mab`, `mod_adc_p`, `mod_adc`, `mod_other_protein_p`, `mod_other_protein`, `mod_vaccine_p`, `mod_vaccine`, `mod_cell_therapy_p`, `mod_cell_therapy`, `mod_gene_therapy_p`, `mod_gene_therapy`, `mod_mrna_lnp_p`, `mod_mrna_lnp`, `mod_rnai_oligo_p`, `mod_rnai_oligo`, `mod_peptide_p`, `mod_peptide`, `mod_small_molecule_p`, `mod_small_molecule`, `mod_plasma_p`, `mod_plasma`, `leaked_marker_stripped`, `state_chars`, `model`

## `data/jev/site_profile_agreement.csv`

20 rows. Columns:

`label`, `name`, `decided`, `jev_yes`, `unsure`, `kw_hit`, `agree`, `agree_pct`, `jev_yes_kw_absent`, `jev_no_kw_present`, `neg`, `lineage`, `other`

## `data/jev/site_profile_disagreements.csv`

364 rows. Columns:

`site_id`, `label`, `direction`, `p`, `bucket`, `field`, `keyword`, `snippet`

## `data/jev/site_verdict_final.csv`

604 rows. Columns:

`site_id`, `account_id`, `verdict`, `reason`, `basis`, `source_url`, `source_date`, `policy_verdict`, `jev_verdict`

## `data/news/account_news.csv`

48 rows. Columns:

`account_id`, `n_items`, `top`

## `data/news/news_items.csv`

6708 rows. Columns:

`item_id`, `account_id`, `query_kind`, `site_id`, `title`, `source`, `url`, `published`, `snippet`, `fetched_at`

## `data/news/news_labels.csv`

6708 rows. Columns:

`item_id`, `account_id`, `query_kind`, `site_id`, `title`, `source`, `url`, `published`, `about_company_p`, `ops_p`, `trigger`, `keep`, `jev_site`, `site_conf`, `site_deal_p`, `closure_p`, `opening_p`, `capex_p`, `manufacturing_deal_p`, `approval_p`, `leadership_p`, `model`

## `data/news/news_queries.csv`

277 rows. Columns:

`account_id`, `query_kind`, `site_id`, `query`, `cache_key`, `status`, `http_status`, `items_in_feed`, `items_in_window`, `fetched_at`

## `data/news/site_news.csv`

91 rows. Columns:

`site_id`, `account_id`, `n_items`, `latest_date`, `latest_trigger`, `latest_title`, `latest_url`, `latest_source`, `triggers`

## `data/pilot/jev_autonomy.csv`

32 rows. Columns:

`posting_id`, `claude`, `jev`, `confidence`, `agree`, `evidence_segment`

## `data/pilot/jev_site_verdict.csv`

604 rows. Columns:

`site_id`, `account_id`, `region`, `short_text`, `leaked_marker`, `gold`, `policy_gold`, `gold_source`, `jev`, `tag`, `unsure_on`, `agree`, `eu_repass`, `business`, `business_conf`, `cdmo`, `drug_product`, `drug_substance`, `manufacturing`, `modality`, `modality_conf`, `pd_msat`, `research`, `status`, `status_conf`, `support`

## `data/pilot/jev_tech.csv`

123 rows. Columns:

`posting_id`, `tool`, `source`, `jev_found`, `jev_p`, `segment`

## `data/pilot/regex_site_date.csv`

32 rows. Columns:

`posting_id`, `site_regex`, `site_claude`, `site_agree`, `date_regex`, `date_claude`, `date_agree`
