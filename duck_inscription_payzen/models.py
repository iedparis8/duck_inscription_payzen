# coding=utf-8
from __future__ import unicode_literals
from django.core.urlresolvers import reverse
from django_payzen.models import PaymentRequest, ThemeConfig, MultiPaymentConfig, CustomPaymentConfig, PaymentResponse, \
    RequestDetails, CustomerDetails, ShippingDetails, OrderDetails, auth_user_model
from duck_inscription.models import Wish
from django_payzen import constants, app_settings, tools
from django_xworkflows import models as xwf_models
from xworkflows import  before_transition, on_enter_state
import datetime

from django.utils.timezone import utc

__author__ = 'paulguichon'

from django.db import models

class MoyenPaiementModel(models.Model):
    """
    chéque virement etc
    """
    type = models.CharField('type paiement', primary_key=True, max_length=3)
    label = models.CharField('label', max_length=60)

    def __unicode__(self):
        return unicode(self.label)


class TypePaiementModel(models.Model):
    """
    Droit univ ou frais péda
    """
    type = models.CharField('type de frais', primary_key=True, max_length=5)
    label = models.CharField('label', max_length=40)


    def __unicode__(self):
        return unicode(self.label)


PRECEDENT = 0
TITLE = 1
NEXT = 2
class PaiementState(xwf_models.Workflow):
    log_model = 'duck_inscription_payzen.PaiementStateLog'
    states = (
        ('droit_univ', 'Droit universitaire'),
        ('choix_demi_annee', 'Inscription aux semestres'),
        ('nb_paiement', "Choisir le nombre de paiements"),
        ('recapitulatif', "Récapitulatif"),
        ('paiement', 'Paiement CB'),
        ('error', 'Erreur'),
        ('failure', 'Failure'),
        ('done', 'Effectué'),
    )
    initial_state = 'droit_univ'

    transitions = (
        ('droit_univ', ('failure', 'error', 'choix_demi_annee', 'nb_paiement'), 'droit_univ'),
        ('choix_demi_annee', ('droit_univ', 'nb_paiement'), 'choix_demi_annee'),
        ('nb_paiement', ('droit_univ', 'choix_demi_annee', 'recapitulatif'),'nb_paiement'),
        ('recapitulatif', 'nb_paiement', 'recapitulatif'),
        ('paiement', ('failure', 'error', 'recapitulatif'), 'paiement'),
        ('error', 'paiement', 'error'),
        ('failure', 'paiement', 'failure'),
        ('done', 'paiement', 'done')
    )
class PaiementStateLog(xwf_models.BaseTransitionLog):
    paiement = models.ForeignKey('PaiementAllModel', related_name='log_paiement')
    MODIFIED_OBJECT_FIELD = 'paiement'


class PaiementAllModel(xwf_models.WorkflowEnabled, models.Model):
    state = xwf_models.StateField(PaiementState)
    moment_paiement = [u"Au moment de l'inscription", u'01/01/15', u'15/02/15']
    wish = models.OneToOneField(Wish)
    moyen_paiement = models.ForeignKey(MoyenPaiementModel, verbose_name=u'Votre moyen de paiement :',
                                       help_text=u"Veuillez choisir un moyen de paiement", null=True)
    nb_paiement_frais = models.IntegerField(verbose_name=u"Nombre de paiements pour les frais pédagogiques", default=1)
    demi_annee = models.BooleanField(default=False)

    @on_enter_state('paiement')
    def on_enter_state_paiement(self, res, *arg, **kwargs):
        if not self.moyen_paiement.type == 'CB':
            self.done()
            self.wish.inscription()

    @before_transition('choix_demi_annee')
    def on_enter_state_choix_demi_annee(self, *args, **kwargs):
        if not self.wish.can_demi_annee():
            if self.state.is_droit_univ:
                self.nb_paiement()
            elif self.state.is_nb_paiement:
                self.droit_univ()
            raise xwf_models.AbortTransition
    @property
    def num_transaction(self):
        pk = str(self.pk)
        return (6-len(pk))*'0'+pk

    def liste_motif(self):
        a = []
        for x in range(self.nb_paiement_frais):
            chaine = u'IED  %s %s %s %s' % (
            self.wish.etape.cod_etp, self.wish.individu.code_opi, self.wish.individu.last_name, str(x + 1))
            a.append(chaine)
        return a

    def range(self):
        a = []
        for x in range(self.nb_paiement_frais):
            a.append((x, self.moment_paiement[x]))
        return a


    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if self.demi_annee and not self.wish.demi_annee:
            self.wish.demi_annee = True
            self.wish.save()
        super(PaiementAllModel, self).save(force_insert, force_update, using, update_fields)

    def previous_step(self):
        index = self.state.workflow.states._order.index(self.state)-1
        if index >= 0:
            prev = self.state.workflow.states._order[self.state.workflow.states._order.index(self.state)-1]
            try:
                getattr(self, prev)()
            except xwf_models.AbortTransition:
                pass
            return True
        else:
            return False

    def recap(self):

        return not self.liste_etapes[self.etape][NEXT]

    def prev(self):
        index = self.state.workflow.states._order.index(self.state)-1
        if index >= 0:
            return True
        else:
            return False

    def template_name(self):
        return 'duck_inscription_payzen/%s.html' % self.state

    def title(self):
        return self.state.title

    def next_step(self):
        index = self.state.workflow.states._order.index(self.state)+1
        if index < len(self.state.workflow.states._order):

            next = self.state.workflow.states._order[index]
            try:
                getattr(self, next)()
            except xwf_models.AbortTransition:
                pass
            self.etape = str(self.state)
            self.save()
            return True
        else:
            return False

    def get_absolute_url(self):
        return reverse(self.state.name, kwargs={'pk':str(self.wish.pk)})

    @property
    def total(self):
        total = self.wish.droit_total() + self.wish.frais_peda()
        return str(int(total*100))

    @property
    def first_paiement(self):
        return str(int((self.wish.droit_total() + (self.wish.frais_peda()/self.nb_paiement_frais))*100))

    def get_context(self):
        pass

    def get_templates(self):
        template = []
        if self.moyen_paiement.type == 'CB':
            template.extend([{'name': 'duck_inscription_payzen/formulaire_paiement_cb.html'}])
        else:
            template.extend([{'name': "duck_inscription_payzen/formulaire_paiement_droit.html"},
         {'name': "duck_inscription_payzen/formulaire_paiement_frais.html"}])
        if self.moyen_paiement.type == 'V':
            template.extend([ {'name': 'duck_inscription_payzen/autorisation_photo.html'}])
        return template

class DuckInscriptionPaymentRequest(RequestDetails, CustomerDetails,
                     OrderDetails, ShippingDetails):
    """Model that contains all Payzen parameters to initiate a payment."""
    user = models.ForeignKey(auth_user_model, blank=True, null=True)
    paiement = models.OneToOneField(PaiementAllModel, related_name='paiement_request')

    vads_capture_delay = models.PositiveIntegerField(blank=True, null=True)
    vads_contrib = models.CharField(
        max_length=255, blank=True, null=True,
        default=app_settings.VADS_CONTRIB)
    vads_payment_cards = models.CharField(
        max_length=127, blank=True, null=True)
    vads_return_mode = models.CharField(
        max_length=12, choices=constants.VADS_RETURN_MODE_CHOICES,
        blank=True, null=True)
    vads_theme_config = models.CharField(
        max_length=255, blank=True, null=True)
    vads_validation_mode = models.CharField(
        choices=constants.VADS_VALIDATION_MODE_CHOICES,
        max_length=1, blank=True, null=True)
    vads_url_success = models.URLField(blank=True, null=True)
    vads_url_referral = models.URLField(blank=True, null=True)
    vads_url_refused = models.URLField(blank=True, null=True)
    vads_url_cancel = models.URLField(blank=True, null=True)
    vads_url_error = models.URLField(blank=True, null=True)
    vads_url_return = models.URLField(blank=True, null=True)
    vads_user_info = models.CharField(max_length=255, blank=True, null=True)
    vads_shop_name = models.CharField(max_length=255, blank=True, null=True)
    vads_redirect_success_timeout = models.PositiveIntegerField(
        blank=True, null=True)
    vads_redirect_success_message = models.CharField(
        max_length=255, blank=True, null=True)
    vads_redirect_error_timeout = models.PositiveIntegerField(
        blank=True, null=True)
    vads_redirect_error_message = models.CharField(
        max_length=255, blank=True, null=True)

    # Relations
    theme = models.ForeignKey(ThemeConfig, blank=True, null=True)
    payment_config = models.ForeignKey(
        MultiPaymentConfig, blank=True, null=True)
    custom_payment_config = models.ManyToManyField(CustomPaymentConfig)

    class Meta:
        verbose_name = "Request"
        unique_together = ("vads_trans_id", "vads_site_id", "vads_trans_date")

    def set_vads_payment_config(self):
        """
        vads_payment_config can be set only after object saving.

        A custom payment config can be set once PaymentRequest saved
        (adding elements to the m2m relationship). As a consequence
        we set vads_payment_config just before sending data elements
        to payzen."""
        if self.paiement.nb_paiement_frais == 1:
            self.vads_payment_config = 'SINGLE'
        else:
            self.vads_payment_config = 'MULTI:first={};count={};period=30'.format(self.paiement.first_paiement, self.paiement.nb_paiement_frais)

    def set_signature(self):
        self.signature = tools.get_signature(self)

    @property
    def response(self):
        try:
            return PaymentResponse.objects.get(
                vads_trans_id=self.vads_trans_id,
                vads_trans_date=self.vads_trans_date,
                vads_site_id=self.vads_site_id)
        except PaymentResponse.DoesNotExist:
            return PaymentResponse.objects.none()

    @property
    def payment_successful(self):
        return self.response and self.response.payment_successful


    def save(self, **kwargs):
        """
        We set up vads_trans_id and theme according to payzen format.

        If fields values are explicitely set by user, we do not override
        their values.
        :param **kwargs:
        """
        if not self.vads_trans_date:
            self.vads_trans_date = datetime.datetime.utcnow().replace(
                tzinfo=utc).strftime("%Y%m%d%H%M%S")
        if not self.vads_trans_id:
            self.vads_trans_id = tools.get_vads_trans_id(
                self.vads_site_id, self.vads_trans_date)
        if self.theme and not self.vads_theme_config:
            self.vads_theme_config = str(self.theme)
        if not self.pk:
            super(DuckInscriptionPaymentRequest, self).save()

        self.set_vads_payment_config()
        self.set_signature()
        super(DuckInscriptionPaymentRequest, self).save()

    def update(self):
        if not self.pk:
            # Prevent bug on filtering m2m relationship
            self.save()
        if not self.vads_trans_date:
            self.vads_trans_date = datetime.datetime.utcnow().replace(
                tzinfo=utc).strftime("%Y%m%d%H%M%S")
        self.set_vads_payment_config()
        self.set_signature()
        self.save()