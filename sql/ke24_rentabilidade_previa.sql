-- Prévia de rentabilidade por empresa, competência, tipo de moeda e moeda.
-- Fórmulas e sinais da aba KE24 database, linha 15, de KE24_database_draft.xlsx.
-- COALESCE(SUM(...), 0) reproduz a contribuição zero de campos vazios na soma.
-- No custo de materiais, use 4RM Inbound Freight (c_4rm_inbound_freight)
-- no lugar de 4RM Sinter parts, conforme o layout atual da extração.
-- Indicadores omitidos até confirmar os nomes técnicos dos insumos abaixo:
--   DC: R&D GA (VV435)
--   DD: R&D BU (VV438)
--   DE: R&D ACIM (VV436)
--   DZ: Sales GA (VV453)
--   EA: Sales BU (VV456)
--   EI: HR GA (VV477)
--   EK: HR GA BU (VV479)
--   EQ: Legal GA (VV489)
-- Indicadores pendentes:
--   BU Expenses
--   BU OP
--   GA/ CI Expenses
--   Plataform OP
--   AAD | AMEC | ACIM Expenses
--   Base OP
--   Net OP
--   EBT
--   Net Profit/Loss

WITH base AS (
  SELECT
    company_code,
    period_key,
    currency_type,
    currency,
    COUNT(*) AS total_lancamentos,
    COALESCE(SUM(revenue), 0) AS ke24_revenue,
    COALESCE(SUM(c_1sales_return), 0) AS ke24_c_1sales_return,
    COALESCE(SUM(c_1freight_recovery), 0) AS ke24_c_1freight_recovery,
    COALESCE(SUM(c_2taxes_cofins), 0) AS ke24_c_2taxes_cofins,
    COALESCE(SUM(licensing_fees), 0) AS ke24_licensing_fees,
    COALESCE(SUM(c_2taxes_ipi), 0) AS ke24_c_2taxes_ipi,
    COALESCE(SUM(c_2taxes_iss), 0) AS ke24_c_2taxes_iss,
    COALESCE(SUM(c_2taxes_pis), 0) AS ke24_c_2taxes_pis,
    COALESCE(SUM(c_2taxes_vat), 0) AS ke24_c_2taxes_vat,
    COALESCE(SUM(c_2taxes_gst), 0) AS ke24_c_2taxes_gst,
    COALESCE(SUM(c_2sallow_volume_rebat), 0) AS ke24_c_2sallow_volume_rebat,
    COALESCE(SUM(c_2sallow_price_reduct), 0) AS ke24_c_2sallow_price_reduct,
    COALESCE(SUM(c_2sallow_trailingrebt), 0) AS ke24_c_2sallow_trailingrebt,
    COALESCE(SUM(qty_discount), 0) AS ke24_qty_discount,
    COALESCE(SUM(c_2slowmovemdiscount), 0) AS ke24_c_2slowmovemdiscount,
    COALESCE(SUM(c_2warrantyfaildiscoun), 0) AS ke24_c_2warrantyfaildiscoun,
    COALESCE(SUM(c_2airfreight_discount), 0) AS ke24_c_2airfreight_discount,
    COALESCE(SUM(other_discounts), 0) AS ke24_other_discounts,
    -COALESCE(SUM(c_4rm_hot_rolled_steel), 0) AS ke24_c_4rm_hot_rolled_steel,
    -COALESCE(SUM(c_4rm_electricalsteel), 0) AS ke24_c_4rm_electricalsteel,
    -COALESCE(SUM(c_4rm_copper_wire), 0) AS ke24_c_4rm_copper_wire,
    -COALESCE(SUM(c_4rm_cast_iron), 0) AS ke24_c_4rm_cast_iron,
    -COALESCE(SUM(c_4rm_inbound_freight), 0) AS ke24_c_4rm_inbound_freight,
    -COALESCE(SUM(c_4rm_plastics), 0) AS ke24_c_4rm_plastics,
    -COALESCE(SUM(c_4rm_aluminium), 0) AS ke24_c_4rm_aluminium,
    -COALESCE(SUM(c_4rm_oils), 0) AS ke24_c_4rm_oils,
    -COALESCE(SUM(c_4rm_electricals), 0) AS ke24_c_4rm_electricals,
    -COALESCE(SUM(c_4rm_packag_returpack), 0) AS ke24_c_4rm_packag_returpack,
    -COALESCE(SUM(c_4rm_purchased_f_good), 0) AS ke24_c_4rm_purchased_f_good,
    -COALESCE(SUM(c_4rm_purchased_comp), 0) AS ke24_c_4rm_purchased_comp,
    -COALESCE(SUM(c_4rm_transf_price_i_c), 0) AS ke24_c_4rm_transf_price_i_c,
    -COALESCE(SUM(c_4rm_material_other), 0) AS ke24_c_4rm_material_other,
    -COALESCE(SUM(c_4vc_direct_labor), 0) AS ke24_c_4vc_direct_labor,
    -COALESCE(SUM(c_4vc_doh_prod_labo), 0) AS ke24_c_4vc_doh_prod_labo,
    -COALESCE(SUM(c_4vc_doh_maint_lab), 0) AS ke24_c_4vc_doh_maint_lab,
    -COALESCE(SUM(c_4vc_utilities), 0) AS ke24_c_4vc_utilities,
    -COALESCE(SUM(c_4vc_repair_main_eq), 0) AS ke24_c_4vc_repair_main_eq,
    -COALESCE(SUM(c_4vc_maintenance_serv), 0) AS ke24_c_4vc_maintenance_serv,
    -COALESCE(SUM(c_4vc_prod_consumable), 0) AS ke24_c_4vc_prod_consumable,
    COALESCE(SUM(c_4vc_taxes_variable), 0) AS ke24_c_4vc_taxes_variable,
    -COALESCE(SUM(c_4vc_othervaroverhead), 0) AS ke24_c_4vc_othervaroverhead,
    -COALESCE(SUM(c_4fc_indirect_labor), 0) AS ke24_c_4fc_indirect_labor,
    -COALESCE(SUM(c_4fc_fixed_labor), 0) AS ke24_c_4fc_fixed_labor,
    -COALESCE(SUM(c_4fc_depreciation), 0) AS ke24_c_4fc_depreciation,
    COALESCE(SUM(c_4fc_taxes_fixed), 0) AS ke24_c_4fc_taxes_fixed,
    -COALESCE(SUM(c_4fc_mnfgstartexptech), 0) AS ke24_c_4fc_mnfgstartexptech,
    -COALESCE(SUM(c_4fc_nonprodconsumabl), 0) AS ke24_c_4fc_nonprodconsumabl,
    -COALESCE(SUM(c_4fc_repairmainfacilt), 0) AS ke24_c_4fc_repairmainfacilt,
    -COALESCE(SUM(c_4fc_otherfixoverhead), 0) AS ke24_c_4fc_otherfixoverhead,
    -COALESCE(SUM(c_5oc_fixothercos_i_c), 0) AS ke24_c_5oc_fixothercos_i_c,
    -COALESCE(SUM(c_5oc_it_expenses), 0) AS ke24_c_5oc_it_expenses,
    -COALESCE(SUM(c_5oc_productliability), 0) AS ke24_c_5oc_productliability,
    -COALESCE(SUM(c_5oc_variableothercos), 0) AS ke24_c_5oc_variableothercos,
    -COALESCE(SUM(c_4vc_material_usage), 0) AS ke24_c_4vc_material_usage,
    -COALESCE(SUM(c_4vc_material_waste), 0) AS ke24_c_4vc_material_waste,
    -COALESCE(SUM(c_4vc_obsolescence), 0) AS ke24_c_4vc_obsolescence,
    -COALESCE(SUM(c_4vc_other_scrap), 0) AS ke24_c_4vc_other_scrap,
    COALESCE(SUM(c_5fw_fixed_cos_other), 0) AS ke24_c_5fw_fixed_cos_other,
    -COALESCE(SUM(c_4fc_royalties), 0) AS ke24_c_4fc_royalties,
    COALESCE(SUM(procurement_operatio), 0) AS ke24_procurement_operatio,
    -COALESCE(SUM(others_cogs_operatio), 0) AS ke24_others_cogs_operatio,
    COALESCE(SUM(c_4fc_other), 0) AS ke24_c_4fc_other,
    -COALESCE(SUM(c_5variable_warranty), 0) AS ke24_c_5variable_warranty,
    COALESCE(SUM(c_5fixed_warranty), 0) AS ke24_c_5fixed_warranty,
    -COALESCE(SUM(ext_failure_operati), 0) AS ke24_ext_failure_operati,
    -COALESCE(SUM(logistic_operations), 0) AS ke24_logistic_operations,
    -COALESCE(SUM(c_5fw_export_expenses), 0) AS ke24_c_5fw_export_expenses,
    COALESCE(SUM(surcharges), 0) AS ke24_surcharges,
    COALESCE(SUM(freight_outbound), 0) AS ke24_freight_outbound,
    COALESCE(SUM(c_5fw_oempurchprodtodc), 0) AS ke24_c_5fw_oempurchprodtodc,
    COALESCE(SUM(c_5fw_secondarydctotp), 0) AS ke24_c_5fw_secondarydctotp,
    COALESCE(SUM(other_freight_ware), 0) AS ke24_other_freight_ware,
    -COALESCE(SUM(c_5fw_variablewarhouse), 0) AS ke24_c_5fw_variablewarhouse,
    -COALESCE(SUM(fixed_warehouse_oper), 0) AS ke24_fixed_warehouse_oper,
    -COALESCE(SUM(sample_cost), 0) AS ke24_sample_cost,
    COALESCE(SUM(sample_taxes), 0) AS ke24_sample_taxes,
    COALESCE(SUM(sample_freight), 0) AS ke24_sample_freight,
    COALESCE(SUM(comission), 0) AS ke24_comission,
    COALESCE(SUM(other_cost_of_sales), 0) AS ke24_other_cost_of_sales,
    -COALESCE(SUM(it_serv_operations), 0) AS ke24_it_serv_operations,
    -COALESCE(SUM(hr_operations), 0) AS ke24_hr_operations,
    -COALESCE(SUM(finance_operations), 0) AS ke24_finance_operations,
    COALESCE(SUM(legal_operations), 0) AS ke24_legal_operations,
    -COALESCE(SUM(others_adm_operation), 0) AS ke24_others_adm_operation,
    -COALESCE(SUM(c_6ro_reestructurecost), 0) AS ke24_c_6ro_reestructurecost,
    COALESCE(SUM(c_6ro_intang_amortiz), 0) AS ke24_c_6ro_intang_amortiz,
    -COALESCE(SUM(pl_sal_fix_assets), 0) AS ke24_pl_sal_fix_assets,
    -COALESCE(SUM(oth_income_expenses), 0) AS ke24_oth_income_expenses,
    -COALESCE(SUM(foreign_exchan_gain), 0) AS ke24_foreign_exchan_gain,
    -COALESCE(SUM(hedge_gain_loss), 0) AS ke24_hedge_gain_loss,
    -COALESCE(SUM(bank_fees), 0) AS ke24_bank_fees,
    COALESCE(SUM(miscellaneous), 0) AS ke24_miscellaneous,
    COALESCE(SUM(discounts_earned), 0) AS ke24_discounts_earned,
    -COALESCE(SUM(interest_expense), 0) AS ke24_interest_expense,
    -COALESCE(SUM(interest_income), 0) AS ke24_interest_income,
    -COALESCE(SUM(income_taxes), 0) AS ke24_income_taxes
  FROM `datalake-pipeline.profitability_data.ke24_processed`
  GROUP BY company_code, period_key, currency_type, currency
),
indicadores_1 AS (
  SELECT
    *,
    ke24_revenue + ke24_c_1sales_return + ke24_c_1freight_recovery AS gross_sales,
    ke24_c_2taxes_cofins + ke24_licensing_fees + ke24_c_2taxes_ipi + ke24_c_2taxes_iss + ke24_c_2taxes_pis + ke24_c_2taxes_vat + ke24_c_2taxes_gst AS taxes_over_sales,
    ke24_c_2sallow_volume_rebat + ke24_c_2sallow_price_reduct + ke24_c_2sallow_trailingrebt + ke24_qty_discount + ke24_c_2slowmovemdiscount + ke24_c_2warrantyfaildiscoun + ke24_c_2airfreight_discount + ke24_other_discounts AS sales_allowances,
    ke24_c_4rm_hot_rolled_steel + ke24_c_4rm_electricalsteel + ke24_c_4rm_copper_wire
      + ke24_c_4rm_cast_iron + ke24_c_4rm_inbound_freight + ke24_c_4rm_plastics
      + ke24_c_4rm_aluminium + ke24_c_4rm_oils + ke24_c_4rm_electricals
      + ke24_c_4rm_packag_returpack + ke24_c_4rm_purchased_f_good + ke24_c_4rm_purchased_comp
      + ke24_c_4rm_transf_price_i_c + ke24_c_4rm_material_other AS material_cost,
    ke24_c_4vc_direct_labor AS direct_labor,
    ke24_c_4vc_doh_prod_labo + ke24_c_4vc_doh_maint_lab AS doh_ind_labor,
    ke24_c_4vc_utilities + ke24_c_4vc_repair_main_eq + ke24_c_4vc_maintenance_serv + ke24_c_4vc_prod_consumable + ke24_c_4vc_taxes_variable + ke24_c_4vc_othervaroverhead AS doh,
    ke24_c_5oc_variableothercos + ke24_c_4vc_material_usage + ke24_c_4vc_material_waste + ke24_c_4vc_obsolescence + ke24_c_4vc_other_scrap + ke24_c_4fc_royalties AS scrap_obsolescence,
    ke24_c_5fw_export_expenses + ke24_surcharges + ke24_freight_outbound + ke24_c_5fw_oempurchprodtodc + ke24_c_5fw_secondarydctotp + ke24_other_freight_ware + ke24_c_5fw_variablewarhouse + ke24_sample_cost + ke24_sample_taxes + ke24_sample_freight + ke24_comission + ke24_c_5variable_warranty + ke24_c_5fixed_warranty + ke24_ext_failure_operati AS sales_variable_cost,
    ke24_c_4fc_indirect_labor + ke24_c_4fc_fixed_labor + ke24_c_4fc_depreciation + ke24_c_4fc_taxes_fixed + ke24_c_4fc_mnfgstartexptech + ke24_c_4fc_nonprodconsumabl + ke24_c_4fc_repairmainfacilt + ke24_c_4fc_otherfixoverhead AS fixed_overhead,
    ke24_c_5oc_fixothercos_i_c + ke24_c_5oc_it_expenses + ke24_c_5oc_productliability + ke24_c_5fw_fixed_cos_other + ke24_procurement_operatio + ke24_others_cogs_operatio + ke24_c_4fc_other + ke24_logistic_operations + ke24_fixed_warehouse_oper + ke24_other_cost_of_sales AS other_fixed_cost,
    ke24_it_serv_operations + ke24_hr_operations + ke24_finance_operations + ke24_legal_operations + ke24_others_adm_operation AS operations_expenses,
    ke24_c_6ro_reestructurecost + ke24_c_6ro_intang_amortiz + ke24_pl_sal_fix_assets + ke24_oth_income_expenses AS other_operating_income_expense,
    ke24_foreign_exchan_gain + ke24_hedge_gain_loss + ke24_bank_fees + ke24_miscellaneous + ke24_discounts_earned + ke24_interest_expense + ke24_interest_income AS other_financial_income_expense,
    ke24_income_taxes AS income_taxes
  FROM base
),
indicadores_2 AS (
  SELECT
    *,
    gross_sales + taxes_over_sales + sales_allowances AS net_sales,
    doh_ind_labor + doh AS variable_overhead
  FROM indicadores_1
),
indicadores_3 AS (
  SELECT
    *,
    material_cost + direct_labor + variable_overhead
      + scrap_obsolescence + fixed_overhead AS product_cost,
    direct_labor + net_sales + material_cost + sales_variable_cost
      + variable_overhead + scrap_obsolescence AS contribution_margin
  FROM indicadores_2
),
indicadores_4 AS (
  SELECT
    *,
    contribution_margin + fixed_overhead + other_fixed_cost AS gross_profit
  FROM indicadores_3
)
SELECT
  company_code,
  period_key,
  currency_type,
  currency,
  total_lancamentos,
  ke24_revenue AS total_revenue,
  gross_sales,
  taxes_over_sales,
  sales_allowances,
  net_sales,
  product_cost,
  material_cost,
  direct_labor,
  variable_overhead,
  doh_ind_labor,
  doh,
  scrap_obsolescence,
  sales_variable_cost,
  contribution_margin,
  fixed_overhead,
  other_fixed_cost,
  gross_profit,
  operations_expenses,
  gross_profit + operations_expenses AS industrial_op,
  other_operating_income_expense,
  other_financial_income_expense,
  income_taxes
FROM indicadores_4
ORDER BY period_key, company_code, currency_type, currency;
