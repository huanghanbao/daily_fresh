from django.conf.urls import url, include

from apps.cart import views

urlpatterns = [

    url(r'^add$', views.AddCartView.as_view(), name='add'),     # 添加商品到购物车
    # /update
    url(r'^update$', views.CartUpdateView.as_view(), name='update'), # 更新购物车商品数量
    # /cart
    url(r'^$', views.CartInfoView.as_view(), name='info'),      # 进入购物车界面
    # 删除购物车数据
    url(r'^delete$', views.CartDeleteView.as_view(), name='delete')


]
