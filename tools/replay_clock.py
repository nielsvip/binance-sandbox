def sim_datetime_class(real_datetime, sim_now, sim_utcnow):
    class SimDatetimeMeta(type(real_datetime)):
        def __instancecheck__(cls, instance):
            return isinstance(instance, real_datetime) or type.__instancecheck__(cls, instance)

    class SimDatetime(real_datetime, metaclass=SimDatetimeMeta):
        @classmethod
        def now(cls, tz=None):
            return sim_now(tz)

        @classmethod
        def utcnow(cls):
            return sim_utcnow()

    return SimDatetime
