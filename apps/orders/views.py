from datetime import datetime
from time import sleep

from django.core.urlresolvers import reverse
from django.db import transaction
from django.http.response import JsonResponse
from django.shortcuts import render, redirect
from django.views.generic import View
from django_redis import get_redis_connection
from redis.client import StrictRedis

from apps.goods.models import GoodsSKU
from apps.orders.models import OrderInfo, OrderGoods
from apps.users.models import Address
from utils.common import LoginRequiredMixin


class PlaceOrderView(LoginRequiredMixin, View):

    def post(self, request):

        # 获取请求参数：sku_ids，count
        count = request.POST.get('count')
        sku_ids = request.POST.getlist('sku_ids')  # 一键多值

        # 校验参数合法性
        if not sku_ids:
            # 如果商品id为空，跳转到购物车界面
            return redirect(reverse('cart:info'))

        # todo: 查询业务数据： 地址，购物车商品，总数量，总金额
        # 获取用户地址信息
        try:
            # Address.objects.filter(user=request.user).order_by('-create_time')[0]
            address = Address.objects.filter(user=request.user).latest('create_time')
        except:
            # 查询不到地址信息，则用户需要点击页面中的按钮，新增地址
            address = None

        skus = []           # 要显示的商品列表
        total_count = 0     # 商品总数量
        total_amount = 0    # 商品总金额

        strict_redis = get_redis_connection() # type: StrictRedis
        # cart_1 = {1: 2, 2: 2}
        key = 'cart_%s' % request.user.id

        # 如果是从购物车页面过来，商品的数量从redis中获取
        if count is None:
            # 循环商品id： sku_ids
            for sku_id in sku_ids:
                # 查询商品对象
                try:
                    sku = GoodsSKU.objects.get(id=sku_id)
                except GoodsSKU.DoesNotExist:
                    # 如果商品id为空，跳转到购物车界面
                    return redirect(reverse('cart:info'))

                # 获取商品数量和小计金额(类型转换)
                count = strict_redis.hget(key, sku_id)  # bytes
                count = int(count)
                amount = sku.price * count

                # 动态地给商品对象新增实例属性(count, amount)
                sku.count = count
                sku.amount = amount

                # 添加商品对象到列表中
                skus.append(sku)

                # 累计商品总数量和总金额
                total_count += sku.count
                total_amount += sku.amount
        else:
            # 如果是从详情页面过来，商品的数量从request中获取（只有一个商品）
            sku_id = request.POST.get('sku_ids')

            # 查询商品对象
            try:
                sku = GoodsSKU.objects.get(id=sku_id)
            except GoodsSKU.DoesNotExist:
                # 如果商品id为空，跳转到购物车界面
                return redirect(reverse('cart:info'))

            # 获取商品数量和小计金额(类型转换)
            count = int(count)
            amount = sku.price * count

            # 判断库存：详情页没有判断库存
            if count > sku.stock:
                # 库存不足，跳转回详情界面
                return redirect(reverse('goods:detail', args=[sku_id]))

            # 动态地给商品对象新增实例属性(count, amount)
            sku.count = count
            sku.amount = amount

            # 添加商品对象到列表中
            skus.append(sku)

            # 累计商品总数量和总金额
            total_count += sku.count
            total_amount += sku.amount

            # 将商品数量保存到`Redis`中（以便取消操作在购物车中还能看得到商品）
            # cart_1 = {1: 2, 2: 2}
            strict_redis.hset(key, sku_id, count)

        # 运费(固定)
        trans_cost = 10
        # 实付金额
        total_pay = total_amount + trans_cost

        # 定义模板显示的字典数据

        # [1,2]  ->  1,2
        sku_ids_str = ','.join(sku_ids)

        context = {
            'skus': skus,
            'address': address,
            'total_count': total_count,
            'total_amount': total_amount,
            'trans_cost': trans_cost,
            'total_pay': total_pay,
            'sku_ids_str': sku_ids_str,
        }

        # 响应结果: 返回确认订单html界面
        return render(request, 'place_order.html', context)


class CommitOrderView(View):
    """提交订单"""

    @transaction.atomic
    def post(self, request):
        # 登录判断
        if not request.user.is_authenticated():
            return JsonResponse({'code': 1, 'errmsg': '请先登录'})

        # 获取请求参数：address_id, pay_method, sku_ids_str
        address_id = request.POST.get('address_id')
        pay_method = request.POST.get('pay_method')
        # 商品id，格式型如： 1,2,3
        sku_ids_str = request.POST.get('sku_ids_str')

        # 校验参数不能为空
        if not all([address_id, pay_method, sku_ids_str]):
            return JsonResponse({'code': 2, 'errmsg': '参数不完整'})

        # 判断地址是否存在
        try:
            address = Address.objects.get(id=address_id)
        except Address.DoesNotExist:
            return JsonResponse({'code': 3, 'errmsg': '地址不能为空'})

        # 创建保存点
        point = transaction.savepoint()
        try:
            # todo: 修改订单信息表: 保存订单数据到订单信息表中(新增一条数据)
            total_count = 0
            total_amount = 0
            trans_cost = 10

            # 时间+用户id
            order_id = datetime.now().strftime('%Y%m%d%H%M%S') + str(request.user.id)
            order = OrderInfo.objects.create(
                order_id=order_id,
                total_count=total_count,
                total_amount=total_amount,
                trans_cost=trans_cost,
                pay_method=pay_method,
                user=request.user,
                address=address,
            )

            # 获取StrictRedis对象: cart_1 = {1: 2, 2: 2}
            strict_redis = get_redis_connection() # type: StrictRedis
            key = 'cart_%s' % request.user.id
            sku_ids = sku_ids_str.split(',')  # str ——> list

            # todo: 核心业务: 遍历每一个商品, 并保存到订单商品表
            for sku_id in sku_ids:
                # 查询订单中的每一个商品对象
                try:
                    sku = GoodsSKU.objects.get(id=sku_id)
                except GoodsSKU.DoesNotExist:
                    # 回滚动到保存点，撤销所有的sql操作
                    transaction.savepoint_rollback(point)

                    return JsonResponse({'code': 4, 'errmsg': '商品不存在'})

                # 获取商品数量，并判断库存
                count = strict_redis.hget(key, sku_id)
                count = int(count)  # bytes -> int
                if count > sku.stock:
                    # 回滚动到保存点，撤销所有的sql操作
                    transaction.savepoint_rollback(point)
                    return JsonResponse({'code': 5, 'errmsg': '库存不足'})

                # todo: 修改订单商品表: 保存订单商品到订单商品表（新增多条数据）
                OrderGoods.objects.create(
                    count=count,
                    price=sku.price,
                    order=order,
                    sku=sku,
                )

                # todo: 修改商品sku表: 减少商品库存, 增加商品销量
                sku.stock -= count
                sku.sales += count
                sku.save()

                # 累加商品数量和总金额
                total_count += count
                total_amount += sku.price * count

                # todo: 修改订单信息表: 修改商品总数量和总金额
            order.total_count = total_count
            order.total_amount = total_amount
            order.save()
        except:
            # 回滚动到保存点，撤销所有的sql操作
            transaction.savepoint_rollback(point)
            return JsonResponse({'code': 6, 'errmsg': '创建订单失败'})

        # 提交事务(保存点)
        transaction.savepoint_commit(point)

        # 从Redis中删除购物车中的商品
        # cart_1 = {1: 2, 2: 2}
        # redis命令: hdel cart_1 1 2
        # 列表 -> 位置参数
        strict_redis.hdel(key, *sku_ids)

        # 订单创建成功， 响应请求，返回json
        return JsonResponse({'code': 0, 'message': '创建订单成功'})


class OrderPayView(View):

    def post(self, request):
        """支付"""
        if not request.user.is_authenticated():
            return JsonResponse({'code': 1, 'errmsg': '请先登录'})

        # 获取请求参数
        order_id = request.POST.get('order_id')
        # 判断合法性
        if not order_id:
            return JsonResponse({'code': 2, 'errmsg': 'order_id不能为空'})

        # 查询订单对象
        try:
            order = OrderInfo.objects.get(order_id=order_id,
                                          status=1,  # 1表示订单是未支付
                                          user=request.user)
        except OrderInfo.DoesNotExist:
            return JsonResponse({'code': 3, 'errmsg': '无效订单，无法支付'})

        # 要支付的总金额 = 商品总金额 + 运费
        total_pay = order.total_amount + order.trans_cost

        # todo: 业务逻辑： 调用第三方sdk，实现支付功能
        from alipay import AliPay

        app_private_key_string = open("apps/orders/app_private_key.pem").read()
        alipay_public_key_string = open("apps/orders/alipay_public_key.pem").read()
        # print(app_private_key_string)
        # print(alipay_public_key_string)

        # 创建AliPay对象
        alipay = AliPay(
            appid="2016091200496790",       # 指定沙箱应用id（后期需要指定为自己创建的应用id）
            app_notify_url=None,  # 默认回调url
            app_private_key_string=app_private_key_string,
            alipay_public_key_string=alipay_public_key_string,  # 支付宝的公钥，验证支付宝回传消息使用，不是你自己的公钥,
            sign_type="RSA2",  # RSA 或者 RSA2  （需要使用RSA2）
            debug=True        # 默认False True表示使用测试环境（沙箱环境）
        )

        # 电脑网站支付，需要跳转到
        # https://openapi.alipay.com/gateway.do? + order_string
        # order_string: 支付串
        order_string = alipay.api_alipay_trade_page_pay(
            out_trade_no=order_id,              # 生鲜网站要支付的订单id
            total_amount=str(total_pay),        # todo: 注意：需要使用字符串类型
            subject='天天生鲜测试订单',
            return_url=None,
            notify_url=None  # 可选, 不填则使用默认notify url
        )

        # notify_url = https://127.0.0.1:8000/orders/check?pay_status=10000&b=2

        # 定义支付引导界面的url地址
        # 正式环境
        # url = 'https://openapi.alipay.com/gateway.do?'  + order_string
        # 沙箱环境: dev
        url = 'https://openapi.alipaydev.com/gateway.do?' + order_string
        return JsonResponse({'code': 0, 'url': url})


class OrderCheckView(View):

    def post(self, request):
        """查询支付结果"""

        if not request.user.is_authenticated():
            return JsonResponse({'code': 1, 'errmsg': '请先登录'})

        # 获取请求参数
        order_id = request.POST.get('order_id')
        # 判断合法性
        if not order_id:
            return JsonResponse({'code': 2, 'errmsg': 'order_id不能为空'})
        # 查询订单对象
        try:
            order = OrderInfo.objects.get(order_id=order_id,
                                          status=1,  # 1表示订单是未支付
                                          user=request.user)
        except OrderInfo.DoesNotExist:
            return JsonResponse({'code': 3, 'errmsg': '无效订单，无法支付'})

        # todo: 业务逻辑： 调用第三方sdk，实现支付功能
        from alipay import AliPay
        app_private_key_string = open("apps/orders/app_private_key.pem").read()
        alipay_public_key_string = open("apps/orders/alipay_public_key.pem").read()
        # print(app_private_key_string)
        # print(alipay_public_key_string)

        # 创建AliPay对象
        alipay = AliPay(
            appid="2016091500513423",       # 指定沙箱应用id（后期需要指定为自己创建的应用id）
            app_notify_url=None,  # 默认回调url
            app_private_key_string=app_private_key_string,
            alipay_public_key_string=alipay_public_key_string,  # 支付宝的公钥，验证支付宝回传消息使用，不是你自己的公钥,
            sign_type="RSA2",  # RSA 或者 RSA2  （需要使用RSA2）
            debug=True        # 默认False True表示使用测试环境（沙箱环境）
        )

        # todo: 调用第三方sdk查询支付结果
        # 返回字典数据
        '''
        {
            "trade_no": "2017032121001004070200176844",
            "code": "10000",
            "invoice_amount": "20.00",
            "open_id": "20880072506750308812798160715407",
            "fund_bill_list": [
              {
                "amount": "20.00",
                "fund_channel": "ALIPAYACCOUNT"
              }
            ],
            "buyer_logon_id": "csq***@sandbox.com",
            "send_pay_date": "2017-03-21 13:29:17",
            "receipt_amount": "20.00",
            "out_trade_no": "out_trade_no15",
            "buyer_pay_amount": "20.00",
            "buyer_user_id": "2088102169481075",
            "msg": "Success",
            "point_amount": "0.00",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "20.00"
          }
        '''
        while(True):
            # 查询订单支付结果
            dict_data = alipay.api_alipay_trade_query(out_trade_no=order_id)

            code = dict_data.get('code')
            trade_status = dict_data.get('trade_status')
            trade_no = dict_data.get('trade_no')  # 支付宝交易号，可以保存到生鲜项目订单信息表字段中

            # 10000接口调用成功
            if code == '10000' and trade_status == 'TRADE_SUCCESS':
                # 订单支付成功,修改订单状态为(待评论)
                order.status = 4
                order.trade_no = trade_no
                order.save()  # 修改订单信息表
                return JsonResponse({'code': 0, 'message': '支付成功'})

            elif (code == '10000' and trade_status == 'WAIT_BUYER_PAY') or code == '40004':
                # WAIT_BUYER_PAY： 等待买家付款
                # 40004: 暂时查询失败，等一会再查询，可能就成功
                sleep(2)
                print(code, trade_status)
                continue
            else:
                # 支付失败
                print(code, trade_status)
                return JsonResponse({'code': 1,  'errmsg': '支付失败：code=%s' % code})













