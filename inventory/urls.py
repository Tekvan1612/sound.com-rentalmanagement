from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    path('', views.custom_login, name='login'),
    path("dashboard/", views.rental_dashboard, name="dashboard"),
    path("api/rental-dashboard-data/", views.rental_dashboard_data, name="rental_dashboard_data"),

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
    path("connects/", views.connects, name="connects"),

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

    path('api/equipment-stock-details/<int:equipment_id>/', views.equipment_stock_details,
         name='equipment_stock_details'),
    path('api/equipment-details/<int:equipment_id>/', views.get_equipment_detail, name='get_equipment_detail'),
    path('transport-master/', views.transport_master_page, name='transport_master'),

    path('add-transport/', views.add_transport, name='add_transport'),
    path('transport-list/', views.transport_list, name='transport_list'),
    path('delete-transport/<int:id>/', views.delete_transport, name='delete_transport'),
    path('delete-transport-document/', views.delete_transport_document),
    path('crew-master/', views.crew_master_page, name='crew_master'),
    path('add-crew/', views.add_crew, name='add_crew'),
    path('crew-list/', views.crew_list, name='crew_list'),
    path('delete-crew/<int:id>/', views.delete_crew, name='delete_crew'),
    path('event-creation/', views.event_creation, name='event_creation'),
    path('add-event/', views.add_event, name='add_event'),
    path('event-list/', views.event_list, name='event_list'),
    path('delete-event/<int:id>/', views.delete_event),

    path("crew-allocation/", views.crew_allocation_page, name="crew_allocation"),
    path("get-events/", views.get_events),
    path("get-employees/", views.get_employees),
    path("get-areas/", views.get_areas),
    path("save-crew-allocation/", views.save_crew_allocation),
    path("get-allocations/", views.get_allocations),

    path("crew-allocation/", views.crew_allocation_page, name="crew_allocation"),
    path("get-events/", views.get_events),
    path("get-employees/", views.get_employees),
    path("get-areas/", views.get_areas),
    path("save-crew-allocation/", views.save_crew_allocation),
    path("get-allocations/", views.get_allocations),
    path("delete-crew-allocation/<int:id>/", views.delete_crew_allocation),
    path('api/warehouses/', views.get_warehouses, name='get_warehouses'),
    path('job-book/', views.job_book_list, name='job_book'),
    path('job-book/add/', views.add_job, name='add_job'),
    path('fetch_client_name/', views.fetch_client_name, name='fetch_client_name'),
    path('fetch_individual_names/', views.fetch_individual_names, name='fetch_individual_names'),
    path('fetch_venue_name/', views.fetch_venue_name, name='fetch_venue_name'),
    path('fetch_venue_address/', views.fetch_venue_address, name='fetch_venue_address'),
    path('fetch_master_categories/', views.fetch_master_categories, name='fetch_master_categories'),
    path('fetch_equipment_names/', views.fetch_equipment_names, name='fetch_equipment_names'),
    path('fetch_rental_price/', views.fetch_rental_price, name='fetch_rental_price'),
    path('get_employee_name/', views.get_employee_name, name='get_employee_name'),
    path('jobs_list/', views.jobs_list, name='jobs_list'),
    path('get_status_counts/', views.get_status_counts, name='get_status_counts'),
    path('update_jobs/<int:id>/', views.update_jobs, name='update_jobs'),
    path('check_equipment_in_temp/', views.check_equipment_in_temp, name='check_equipment_in_temp'),
    path('get_crew_designations/', views.get_crew_designations, name='get_crew_designations'),
    path('get_vehicle_numbers/', views.get_vehicle_numbers, name='get_vehicle_numbers'),
    path('get_driver_list/', views.get_driver_list, name='get_driver_list'),
    path('get_categories/', views.get_categories, name='get_categories'),
    path('fetch_equipment_usages/', views.fetch_equipment_usages, name='fetch_equipment_usages'),
    path(
        "get_serial_details/<int:equipment_id>/",
        views.get_serial_details,
        name="get_serial_details"
    ),
    path("get-connects/", views.get_connects),
    path("save-connect/", views.save_connect),
    path(
        'fetch-vehicle-list/',
        views.fetch_vehicle_list,
        name='fetch_vehicle_list'
    ),
    path('crew-master/', views.crew_master_page, name='crew_master'),
    path('crew-list/', views.crew_list, name='crew_list'),
    path('add-crew/', views.add_crew, name='add_crew'),
    path('update-crew/<int:crew_id>/', views.update_crew, name='update_crew'),
    path('delete-crew/<int:crew_id>/', views.delete_crew, name='delete_crew'),
    path(
        'get-equipment-meta/<int:equipment_id>/',
        views.get_equipment_meta,
        name='get_equipment_meta'
    ),
    path('split-jobs-list/<int:job_id>/', views.split_jobs_list, name='split_jobs_list'),
    path('create-split-job/<int:job_id>/', views.create_split_job, name='create_split_job'),

    path(
        "get-split-jobs/<int:parent_job_id>/",
        views.get_split_jobs,
        name="get_split_jobs"
    ),
    path(
        'get-equipment-details/<int:equipment_id>/',
        views.get_equipment_details,
        name='get_equipment_details'
    ),
    path(
        'delete-connect/<int:id>/',
        views.delete_connect,
        name='delete_connect'
    ),

    path("inactive-job/", views.inactive_job, name="inactive_job"),

    path("scanning/", views.scanning_page, name="scanning_page"),

    path("api/delivery-challan-jobs/", views.delivery_challan_jobs_api, name="delivery_challan_jobs_api"),
    path("api/delivery-challan-equipment/<int:job_id>/", views.delivery_challan_equipment_api,
         name="delivery_challan_equipment_api"),
    path("api/scan-barcode/", views.scan_barcode_api, name="scan_barcode_api"),

    path('dispatch-loading/', views.dispatch_loading, name='dispatch_loading'),

    path("dispatch-loading/", views.dispatch_loading, name="dispatch_loading"),
    path("api/dispatch-jobs/", views.dispatch_jobs_api, name="dispatch_jobs_api"),
    path("api/dispatch-job-equipment/<int:job_id>/", views.dispatch_job_equipment, name="dispatch_job_equipment_api"),
    path("api/dispatch-scanned-list/<int:job_id>/", views.dispatch_scanned_list_api, name="dispatch_scanned_list_api"),
    path("api/dispatch-scan/", views.dispatch_scan_api, name="dispatch_scan_api"),
    path('job-return/', views.job_return, name='job_return'),
    path('quick-return/', views.quick_return, name='quick_return'),
    path("api/job-sections/<int:job_id>/", views.job_sections, name="job_sections"),

    path('warehouse-transfer/', views.warehouse_transfer, name='warehouse_transfer'),

    path('maintenance-out/', views.maintenance_out, name='maintenance_out'),
    path('maintenance-return/', views.maintenance_return, name='maintenance_return'),

    path('damage-missing/', views.damage_missing, name='damage_missing'),

    path("api/return-jobs/", views.return_jobs_api, name="return_jobs_api"),
    path("api/return-scanned-items/<str:job_no>/", views.return_scanned_items_api, name="return_scanned_items_api"),

    path("api/scan-job-return/", views.scan_job_return_api, name="scan_job_return_api"),

    path("api/quick-return-scan/", views.quick_return_scan_api, name="quick_return_scan_api"),

    path(
        "module-list/",
        views.module_list,
        name="module_list"
    ),
    path(
        "quotation-download/<int:job_id>/<str:download_type>/",
        views.quotation_download,
        name="quotation_download"
    ),

    path(
        "proforma-download/<int:job_id>/<str:download_type>/",
        views.proforma_download,
        name="proforma_download"
    ),

    path(
        "prepsheet-download/<int:job_id>/",
        views.prepsheet_download,
        name="prepsheet_download"
    ),

    path(
        "delivery-challan-download/<int:job_id>/",
        views.delivery_challan_download,
        name="delivery_challan_download"
    ),
    path("job-summary/", views.job_summary, name="job_summary"),
    path("api/job-summary-data/", views.job_summary_data, name="job_summary_data"),
]
