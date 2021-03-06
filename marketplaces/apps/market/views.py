import datetime
import logging
import random

from django.conf import settings
from django.core.paginator import Paginator, InvalidPage, EmptyPage
from django.core.urlresolvers import reverse
from django.core.mail import send_mail, EmailMessage
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponseRedirect, Http404, HttpResponse
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from django.utils.translation import ugettext as _
from django.views.decorators.cache import cache_page

from haystack.query import SearchQuerySet
from market.models import MarketSubCategory, MarketCategory, ContactFormInfo
from market.forms import ContactForm
from auth.decorators import login_required

PRODUCTS_PER_PAGE = 16
ITEMS_PER_PAGE = 20
LOTS_PER_PAGE = 20


@cache_page(60 * 5)
def home(request):
    from shops.models import Shop
    from inventory.models import Product
    from market_buy.models import MarketPlacePick, DealerPick
    from market.forms import MarketMailingListMemberForm
    
    marketplace = request.marketplace
    market_place_picks = MarketPlacePick.get_available_picks(marketplace)
    featured_dealers = DealerPick.objects.filter(marketplace=marketplace).order_by("order")[:2]
    recently_products = Product.objects.filter(shop__marketplace=marketplace, has_image=True).order_by("-date_time")[:20]
    
    if request.method == "POST":
        form = MarketMailingListMemberForm(request.POST)
        if form.is_valid():
            member = form.save(commit=False)
            member.marketplace = request.marketplace
            member.save()
            request.flash['message'] = unicode(_("Email successfully registered."))
            request.flash['severity'] = "success"
            return HttpResponseRedirect(reverse("market_home"))
    else:
        form = MarketMailingListMemberForm()
        
    return render_to_response("%s/home.html" % request.marketplace.template_prefix, 
                              {
                               'market_place_picks' : market_place_picks,
                               'featured_dealers' : featured_dealers,
                               'recently_products' : recently_products,
                               'newsletter_form': form
                               }, 
                              RequestContext(request))

def search(request, category_slug=None, subcategory_slug=None, shop_id=None):
    from inventory.models import Product
    
    marketplace = request.marketplace
    
    sqs = SearchQuerySet().models(Product).load_all()
    if shop_id:
        sqs = sqs.filter(marketplace_id=marketplace.id, shop_id=shop_id)
    else:
        sqs = sqs.filter(marketplace_id=marketplace.id)

    current_category = None
    current_subcategory = None

    if category_slug:
        current_category = get_object_or_404(
            MarketCategory, slug=category_slug, marketplace=marketplace)
        # narrow search by category_name
        sqs = sqs.filter(category_id=current_category.id)

        if subcategory_slug:
            current_subcategory = get_object_or_404(
                MarketSubCategory, slug=subcategory_slug, 
                parent__id=current_category.id, marketplace=marketplace)
            # narrow search results by subcategory
            sqs = sqs.filter(subcategory_id=current_subcategory.id)

    else:
        category_name = request.GET.get("category", None)
        
        if category_name and category_name != "All Categories":
            sqs = sqs.filter(category__name=category_name)
            # only search for a category when there's a valid category name       
            current_category = get_object_or_404(
                MarketCategory, name=category_name, marketplace=marketplace)

    if current_category and current_category.slug == 'small-cents':
        small_cents_dealers = current_category.related_shops()
    else:
        small_cents_dealers = None

    getvars = encodevars(request)

    search_text = request.GET.get("q", None)
    if search_text and search_text.strip():        
        sqs = sqs.filter(summary=search_text)
        
    sort_mode = request.session.get("sort_mode", "title")
    
    if sort_mode == "recent":
        sqs = sqs.order_by("-added_at")
    if sort_mode == "oldest":
        sqs = sqs.order_by("added_at")
    if sort_mode == "title":
        sqs = sqs.order_by("title")
    if sort_mode == "-title":
        sqs = sqs.order_by("-title")
    if sort_mode == "price":
        sqs = sqs.order_by("price")
    if sort_mode == "-price":
        sqs = sqs.order_by("-price")
    pager = Paginator(sqs, PRODUCTS_PER_PAGE)

    try:
        page = int(request.GET.get('page','1'))
    except:
        page = 1

    try:
        paginator = pager.page(page)
    except (EmptyPage, InvalidPage):
        raise Http404

    paged = (pager.num_pages > 1)
    
    return render_to_response("%s/search.html" % request.marketplace.template_prefix, 
        {'current_category' : current_category, 'current_subcategory': current_subcategory,
        'products' : paginator, 'pager':pager,
        'paged': paged, 'total': pager.count, 'getvars': getvars,
        'sort_mode' : sort_mode,
        'view_mode' : request.session.get("view_mode", "list"),
        'small_cents_dealers': small_cents_dealers},
        RequestContext(request))

def view_item(request, product_id):
    from inventory.models import Product

    marketplace = request.marketplace
    product = get_object_or_404(Product, shop__marketplace=marketplace, id=product_id)
    shop_categories = product.shop.categories_list()
    related_shops = product.category.related_shops()
#    related_shops.remove(product.shop)
    shop_transactions = product.shop.total_transactions()
    
    images = []
    if hasattr(product, 'item'):
        for image in product.item.imageitem_set.all():
            images.append(image)
        price = product.item.price
    elif hasattr(product, 'lot'):
        for image in product.lot.imagelot_set.all():
            images.append(image)
        price = product.lot.price()

    return render_to_response("%s/view_item.html" % marketplace.template_prefix,
        { 'product': product,
          'images': images,
          'price': price,
          'shop_categories': shop_categories,
          'related_shops': related_shops,
          'shop_transactions': shop_transactions },
        RequestContext(request))

def encodevars(request):
    from django.http import QueryDict
    dic = (request.GET).copy()
    if dic.get("page", None):
        dic.pop("page")
    st = dic.urlencode()
    return st
    
def set_listing_mode(request):
    next = request.GET.get('next', '/')
    mode = request.GET.get('mode', 'gallery')
    request.session['view_mode'] = mode
    return HttpResponseRedirect(next)


def set_order_mode(request):
    next = request.GET.get('next', '/')
    order = request.GET.get('sort', 'title')
    request.session['sort_mode'] = order
    return HttpResponseRedirect(next)


def auctions(request):
    from lots.models import Lot
    marketplace = request.marketplace
    lot_list = Lot.objects.filter(shop__marketplace=marketplace)
    
    pager = Paginator(lot_list, LOTS_PER_PAGE)
    try:
        page = int(request.GET.get('page','1'))
    except:
        page = 1
    try:
        products = pager.page(page)
    except (EmptyPage, InvalidPage):
        products = pager.page(pager.num_pages)
    paged = (pager.num_pages > 1)
    
    return render_to_response("%s/auctions.html" % request.marketplace.template_prefix, 
                              {
                               'lots' : products,
                               'pages': pager.page_range,
                               'paged': paged,
                               'total': pager.count
                              } , 
                              RequestContext(request))
    
def for_sale(request):
    from for_sale.models import Item
    marketplace = request.marketplace
    item_list = Item.objects.filter(shop__marketplace=marketplace, qty__gte=1)
    
    pager = Paginator(item_list, ITEMS_PER_PAGE)
    try:
        page = int(request.GET.get('page','1'))
    except:
        page = 1
    try:
        products = pager.page(page)
    except (EmptyPage, InvalidPage):
        products = pager.page(pager.num_pages)
    paged = (pager.num_pages > 1)
    
    return render_to_response("%s/for_sale.html" % request.marketplace.template_prefix, 
                              {
                               'items' : products,
                               'pages': pager.page_range,
                               'paged': paged,
                               'total': pager.count
                              } , 
                              RequestContext(request))


def sell(request):
    return render_to_response("%s/sell.html" % request.marketplace.template_prefix, 
                              {} , RequestContext(request))

def buy(request):
    
    from inventory.models import Product
    
    marketplace = request.marketplace
    recently_products = Product.objects.filter(shop__marketplace=marketplace, has_image=True).order_by("-date_time")[:20]
    
    return render_to_response("%s/buy.html" % request.marketplace.template_prefix, 
                              {'recently_products' : recently_products} , RequestContext(request))

def community(request):
    return render_to_response("%s/community.html" % request.marketplace.template_prefix, 
                              {} , RequestContext(request))

@login_required
def add_post_comment(request):
    from market.models import MarketBlogPost
    from market.forms import MarketPostCommentForm
    
    if request.method == "POST":
        form = MarketPostCommentForm(request.POST)
        post =  MarketBlogPost.objects.filter(id=request.POST.get("post")).get()
        
        if form.is_valid():
            comment = form.save(commit = False)
            comment.user = request.user
            comment.post = post
            comment.save()
            
    return HttpResponseRedirect(reverse("market_blog"))

def view_post(request, post_slug):
    from market.forms import MarketPostCommentForm
    from market.models import MarketBlogPost
    
    form = MarketPostCommentForm()
    try:
        post = MarketBlogPost.objects.filter(slug=post_slug).get()
    except MarketBlogPost.DoesNotExist:
        return HttpResponse("403")
    
    return render_to_response("%s/post.html" % request.marketplace.template_prefix, 
                              {'post' : post, 'form': form}, RequestContext(request))
                          

def blog(request):
    from market.forms import MarketPostCommentForm
    from market.models import MarketBlogPost, MarketPostComment, MarketPostPick
    
    marketplace = request.marketplace
    
    all_posts = MarketBlogPost.objects.filter(marketplace=marketplace).order_by("-posted_on")
    latest_posts = all_posts[:5]
    latest_comments = MarketPostComment.objects.filter(post__marketplace=marketplace).order_by("-commented_on")[:5]
    picks = MarketPostPick.objects.filter(marketplace=marketplace)
    
    archive = []
    for post in all_posts:
        archive.append({'title': post.title, 'slug': post.slug, 'month' : post.posted_on.strftime("%B")})
    
    form = MarketPostCommentForm()
    
    pager = Paginator(all_posts, 2)
    try:
        page = int(request.GET.get('page','1'))
    except:
        page = 1
    try:
        posts = pager.page(page)
    except (EmptyPage, InvalidPage):
        posts = pager.page(pager.num_pages)
    paged = (pager.num_pages > 1)
    
    return render_to_response("%s/blog.html" % request.marketplace.template_prefix, 
                              {
                               'archive' : archive,
                               'picks' : picks,
                               'posts' : posts,
                               'latest_posts' : latest_posts,
                               'latest_comments' : latest_comments, 
                               'form' : form,
                               'pages': pager.page_range,
                               'paged': paged,
                               'total': pager.count,
                               } , RequestContext(request))

def contact_us(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        market_admin = request.marketplace.contact_email
        if form.is_valid():
            name = form.cleaned_data['name']
            email = form.cleaned_data['email']
            phone = form.cleaned_data['phone']
            message = form.cleaned_data['message']

            msg = "Message from %s (email %s, phone %s).\n%s" % (name, email, phone, message)
            mail = EmailMessage(subject='Contact Form From %s' % request.marketplace,
                                body=msg,
                                from_email=settings.EMAIL_FROM,
                                to=[mail for (name, mail) in settings.STAFF]+[market_admin],
                                headers={'X-SMTPAPI': '{\"category\": \"Contact Form\"}'})
            mail.send(fail_silently=True)

            return HttpResponseRedirect(reverse("market_home"))
        else:
            if form['captcha'].errors:
                email = form.data.get('email', None)
                ip = request.META['REMOTE_ADDR']
                contact_form_info, created = ContactFormInfo.objects.get_or_create(marketplace=request.marketplace, email=email, ip=ip)
                some_time = datetime.datetime.now() - datetime.timedelta(seconds=120)
                if not created and contact_form_info.datetime < some_time:
                    msg = 'Bad captcha posted from %s\nUser email: %s\n\nForm info\nName: %s\nPhone: %s\nMessage: %s' \
                         %(ip, \
                           form.data['email'] or 'unknown', \
                           form.data['name'] or 'unknown', \
                           form.data['phone'] or 'unknown', \
                           form.data['message'] or 'unknown')

                    mail = EmailMessage(subject='Contact Form, bad captcha',
                                        body=msg,
                                        from_email=settings.EMAIL_FROM,
                                        to=[market_admin],
                                        headers={'X-SMTPAPI': '{\"category\": \"Error\"}'})
                    mail.send(fail_silently=True)
    else:
        form = ContactForm()

    return render_to_response("%s/contact_us.html" % request.marketplace.template_prefix, 
                              {'form': form} , RequestContext(request))

def survey(request):
    return render_to_response("%s/survey.html" % request.marketplace.template_prefix, 
                              {} , RequestContext(request))

def sitemap(request, sitemap_id=None):
    if sitemap_id:
        sitemap_template = "%s/sitemap%s.xml" % (request.marketplace.template_prefix, sitemap_id) 
    else:
        sitemap_template = "%s/sitemap.xml" % request.marketplace.template_prefix
    
    return render_to_response(sitemap_template, { 'base_url': request.build_absolute_uri(reverse("market_home")) },
                              RequestContext(request))

def sitemap_index(request):
    return render_to_response("%s/sitemap_index.xml" % request.marketplace.template_prefix, { 'base_url': request.build_absolute_uri(reverse("market_home")) },
                              RequestContext(request))

def sitemap_products(request):
    from inventory.models import Product

    products = Product.objects.filter(shop__marketplace=request.marketplace)
    return render_to_response("%s/sitemap_products.xml" % request.marketplace.template_prefix,
                              { 'base_url': request.build_absolute_uri(reverse("market_home")).rstrip('/'),
                                'products':  products },
                              RequestContext(request))

def robot(request):
    return render_to_response("%s/robots.txt" % request.marketplace.template_prefix, 
                              {} , RequestContext(request))
