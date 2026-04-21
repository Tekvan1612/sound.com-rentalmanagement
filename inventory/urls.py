from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    path('', views.custom_login, name='login'),
    path('dashboard/', views.dashboard, name='dashboard'),

    path('user-master/', views.add_user, name='add_user'),
    path('user-list/', views.user_list, name='user_list'),
    path('update_user/<int:user_id>/', views.update_user, name='update_user'),
    path('delete_user/<int:id>/', views.delete_user, name='delete_user'),

    path('employee-master/', views.add_employee, name='add_employee'),
    path('employee-list/', views.employee_list, name='employee_list'),
    path('modify-employee/', views.modify_employee, name='modify_employee'),

    path('asset-entry/', views.asset_entry, name='asset_entry'),

    path('api/categories/', views.category_list, name='category_list'),
    path('api/category/add/', views.add_category, name='add_category'),
    path('api/category/update/<int:category_id>/', views.update_category, name='update_category'),

    path('api/category/delete/<int:category_id>/', views.delete_category),

    path('api/category-dropdown/', views.category_dropdown, name='category_dropdown'),
    path('api/category/<int:category_id>/', views.get_category_dropdown, name='get_category_dropdown'),

    path('api/subcategories/', views.subcategory_list, name='subcategory_list_all'),
    path('api/subcategories/<int:category_id>/', views.subcategory_list, name='subcategory_list'),
    path('api/subcategory/add/', views.add_subcategory, name='add_subcategory'),
    path('api/subcategory/update/<int:id>/', views.update_subcategory, name='update_subcategory'),
    path('api/subcategory/delete/<int:id>/', views.delete_subcategory, name='delete_subcategory'),

    path('add_equipment/', views.add_equipment, name='add_equipment'),
    path('equipment_list/', views.equipment_list, name='equipment_list'),
    path('equipment_list/<int:subcategory_id>/', views.equipment_list, name='equipment_list'),
    path('edit_subcategory_dropdown/', views.edit_subcategory_dropdown, name='edit_subcategory_dropdown'),
    path('edit_get_category_name/', views.edit_get_category_name, name='edit_get_category_name'),
    path('fetch_stock_status/<int:equipment_id>/', views.fetch_stock_status, name='fetch_stock_status'),
    path('fetch_serial_barcode_no/<int:equipment_id>/', views.fetch_serial_barcode_no,
         name='fetch_serial_barcode_no'),
    path('get_dimension_list/<int:equipment_id>/', views.get_dimension_list, name='get_dimension_list'),
    path('insert_vendor/', views.insert_vendor, name='insert_vendor'),
    path('subcategory_dropdown/', views.subcategory_dropdown, name='subcategory_dropdown'),
    path('get_category_name/', views.get_category_name, name='get_category_name'),
    path('Stock_list/', views.stock_list, name='Stock_list'),
    path('fetch-equipment-list/', views.fetch_stock_equipment_list, name='fetch_stock_equipment_list'),
    path('stock-in/<int:equipment_id>/', views.stock_in, name='stock_in'),
    path('update-stock-in/<int:row_id>/', views.update_stock_in, name='update-stock-in'),
    path('fetch_stock_details_by_name/', views.fetch_stock_details_by_name,
         name='fetch_stock_details_by_name'),

    path('api/equipment-stock-details/<int:equipment_id>/', views.equipment_stock_details,
         name='equipment_stock_details'),
    path('api/stock-details/update/<int:row_id>/', views.update_stock_inline, name='update_stock_inline'),
    path('api/stock-details/delete/<int:row_id>/', views.delete_stock_detail, name='delete_stock_detail'),
    path('api/equipment-stock-details/<int:equipment_id>/', views.equipment_stock_details,
         name='equipment_stock_details'),
    path('api/equipment-details/<int:equipment_id>/', views.get_equipment_detail, name='get_equipment_detail'),
    path('update-equipment/<int:equipment_id>/', views.update_equipment, name='update_equipment'),

    path('delete-equipment/', views.delete_equipment_id, name='delete_equipment_id'),
    path('logout/', views.logout_view, name='logout'),

    path('company-warehouse-master/', views.company_warehouse_master_page, name='company_warehouse_master_page'),

    path('company-list/', views.company_list, name='company_list'),
    path('company-add/', views.add_company, name='add_company'),
    path('company-update/<int:id>/', views.update_company, name='update_company'),
    path('company-delete/<int:id>/', views.delete_company, name='delete_company'),

    path('warehouse-list/', views.warehouse_list, name='warehouse_list'),
    path('warehouse-add/', views.add_warehouse, name='add_warehouse'),
    path('warehouse-update/<int:id>/', views.update_warehouse, name='update_warehouse'),
    path('warehouse-delete/<int:id>/', views.delete_warehouse, name='delete_warehouse'),
    path('warehouse/<int:id>/', views.get_warehouse_by_id, name='get_warehouse_by_id'),
]
