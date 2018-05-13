from django.conf.urls import url, include

from apps.orders import views

urlpatterns = [

    # /orders/place 确认订单界面
    url(r'^place$', views.PlaceOrderView.as_view(), name='place'),
    # 订单提交 /orders/commit
    url(r'^commit$', views.CommitOrderView.as_view(), name='commit'),
    # 支付: /orders/pay
    url(r'^pay$', views.OrderPayView.as_view(), name='pay'),
    # 查询支付结果: /orders/check
    url(r'^check', views.OrderCheckView.as_view(), name='check'),
]
