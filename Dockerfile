FROM nginx:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
    luajit \
    libluajit-5.1-dev \
    git \
    make \
    gcc \
    libpcre3-dev \
    zlib1g-dev \
    libssl-dev     && apt-get install -y --no-install-recommends luarocks     && luarocks install lua-resty-redis     && rm -rf /var/lib/apt/lists/*

# Download and compile ngx_http_lua_module
RUN git clone https://github.com/openresty/lua-nginx-module.git /usr/local/src/lua-nginx-module
RUN git clone https://github.com/simpl/ngx_devel_kit.git /usr/local/src/ngx_devel_kit

RUN wget http://nginx.org/download/nginx-1.24.0.tar.gz -O /tmp/nginx.tar.gz \
    && tar -zxvf /tmp/nginx.tar.gz -C /tmp \
    && cd /tmp/nginx-1.24.0 \
    && ./configure --prefix=/etc/nginx \
    --sbin-path=/usr/sbin/nginx \
    --modules-path=/usr/lib/nginx/modules \
    --conf-path=/etc/nginx/nginx.conf \
    --error-log-path=/var/log/nginx/error.log \
    --http-log-path=/var/log/nginx/access.log \
    --pid-path=/var/run/nginx.pid \
    --lock-path=/var/run/nginx.lock \
    --http-client-body-temp-path=/var/cache/nginx/client_temp \
    --http-proxy-temp-path=/var/cache/nginx/proxy_temp \
    --http-fastcgi-temp-path=/var/cache/nginx/fastcgi_temp \
    --http-uwsgi-temp-path=/var/cache/nginx/uwsgi_temp \
    --http-scgi-temp-path=/var/cache/nginx/scgi_temp \
    --with-compat \
    --with-file-aio \
    --with-threads \
    --with-http_addition_module \
    --with-http_auth_request_module \
    --with-http_dav_module \
    --with-http_flv_module \
    --with-http_gunzip_module \
    --with-http_gzip_static_module \
    --with-http_mp4_module \
    --with-http_random_index_module \
    --with-http_realip_module \
    --with-http_secure_link_module \
    --with-http_slice_module \
    --with-http_ssl_module \
    --with-http_stub_status_module \
    --with-http_sub_module \
    --with-http_v2_module \
    --with-mail \
    --with-mail_ssl_module \
    --with-stream \
    --with-stream_realip_module \
    --with-stream_ssl_module \
    --with-stream_ssl_preread_module \
    --with-pcre \
    --with-pcre-jit \
    --with-debug \
    --add-module=/usr/local/src/ngx_devel_kit \
    --add-module=/usr/local/src/lua-nginx-module \
    && make \
    && make install \
    && rm -rf /tmp/nginx-1.24.0 /tmp/nginx.tar.gz

# Copy Nginx configuration and WAF Lua script
COPY nginx.conf /etc/nginx/nginx.conf
COPY waf.lua /etc/nginx/waf.lua

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
